"""Counter POS: /pos (billing screen), /pos/bills (today's bills + close summary), /pos/bill/{id} (GST invoice)."""
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.clock import business_tz, business_today
from app.core.database import get_db
from app.services import pos
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["pos"])


@router.get("/pos", response_class=HTMLResponse)
def pos_screen(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    s = pos.settings(db)
    return _tmpl(request, "pos.html", {"user": user, "menu": pos.menu_for_pos(db), "ps": s,
                                       "gst_rate": float(s["gst_rate"] or 0), "inclusive": s["prices_include_gst"] == "1"})


@router.post("/pos/bill")
async def pos_create_bill(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    try:
        body = await request.json()
        disc = Decimal(str(body.get("discount") or 0))
        if disc < 0:
            raise ValueError("Discount can't be negative")
        bill_id = pos.create_bill(
            db, user.id, body.get("items") or [], body.get("order_type", ""), body.get("pay_mode", ""), disc,
            str(body.get("table") or ""), str(body.get("cust_name") or ""), str(body.get("cust_phone") or ""),
            str(body.get("cust_gstin") or ""))
    except (ValueError, TypeError, InvalidOperation, KeyError) as exc:
        db.rollback()
        return JSONResponse({"error": str(exc) or "Check the bill and try again"}, status_code=400)
    return JSONResponse({"ok": True, "id": bill_id, "url": f"/pos/bill/{bill_id}"})


@router.post("/pos/preview")
async def pos_preview(request: Request, db: Session = Depends(get_db)):
    """Server-side totals so the screen and the printed bill can never disagree."""
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    from sqlalchemy import text
    body = await request.json()
    ids = [int(i["id"]) for i in body.get("items") or []]
    prices = {r[0]: Decimal(str(r[1])) for r in db.execute(text("SELECT id, price FROM menu_items WHERE id = ANY(:i)"), {"i": ids})}
    s = pos.settings(db)
    try:
        disc = Decimal(str(body.get("discount") or 0))
        c = pos.price([(prices[int(i["id"])], max(int(i["qty"]), 0)) for i in body["items"] if int(i["id"]) in prices],
                      disc, Decimal(s["gst_rate"] or 0), s["prices_include_gst"] == "1")
    except (InvalidOperation, ValueError, TypeError):
        return JSONResponse({"error": "Bad amount"}, status_code=400)
    return JSONResponse({k: float(v) for k, v in c.items()})


@router.get("/pos/bills", response_class=HTMLResponse)
def pos_bills(request: Request, day: str = "", db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    try:
        d = date.fromisoformat(day) if day else business_today()
    except ValueError:
        d = business_today()
    v = pos.day_view(db, d)
    for b in v["bills"]:
        b["created_at"] = b["created_at"].astimezone(business_tz())
    return _tmpl(request, "pos_bills.html", {"user": user, "day": d, "today": business_today(), **v})


@router.get("/pos/bill/{bill_id}", response_class=HTMLResponse)
def pos_bill(bill_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    b, items = pos.get_bill(db, bill_id)
    if b is None:
        return RedirectResponse("/pos/bills", status_code=303)
    b["created_at"] = b["created_at"].astimezone(business_tz())
    try:  # TEMPORARY diagnostic (owner-only page): show the real error instead of the masked 500 page
        from app.services.shell_pulse import get_shell_context
        from app.web.deps import templates
        ctx = {"request": request, "user": user, "bill": b, "items": items, "ps": pos.settings(db),
               "types": pos.ORDER_TYPES, "shell": get_shell_context()}
        return HTMLResponse(templates.get_template("pos_bill.html").render(ctx))
    except Exception:
        import sys, traceback
        from fastapi.responses import PlainTextResponse
        msg = "\n".join(["POS bill error (send this to Claude):", "", traceback.format_exc(), "python " + sys.version])
        return PlainTextResponse(msg, status_code=200)


@router.post("/pos/bill/{bill_id}/void")
def pos_void(bill_id: int, request: Request, reason: str = Form(""), db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    if not reason.strip():
        return RedirectResponse(f"/pos/bill/{bill_id}?error=reason", status_code=303)
    pos.void_bill(db, bill_id, reason)
    return RedirectResponse(f"/pos/bill/{bill_id}", status_code=303)


@router.get("/pos/settings", response_class=HTMLResponse)
def pos_settings_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    return _tmpl(request, "pos_settings.html", {"user": user, "ps": pos.settings(db)})


@router.post("/pos/settings")
def pos_settings_save(
    request: Request, legal_name: str = Form(""), gstin: str = Form(""), address: str = Form(""), phone: str = Form(""),
    gst_rate: str = Form("5"), prices_include_gst: str = Form(""), invoice_prefix: str = Form("URS"),
    footer: str = Form(""), db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    try:
        rate = Decimal(gst_rate)
        if not (0 <= rate <= 28):
            raise InvalidOperation
    except InvalidOperation:
        return RedirectResponse("/pos/settings?error=rate", status_code=303)
    pos.save_settings(db, {"legal_name": legal_name, "gstin": gstin.upper(), "address": address, "phone": phone,
                           "gst_rate": rate, "prices_include_gst": "1" if prices_include_gst else "0",
                           "invoice_prefix": invoice_prefix.strip().upper() or "URS", "footer": footer})
    return RedirectResponse("/pos/settings?saved=1", status_code=303)
