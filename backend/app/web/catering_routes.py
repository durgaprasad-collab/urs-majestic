"""Catering: orders for events and functions, from quote to paid, taken in
the admin. Logic (money, ingredients, Buy) in services/catering.py. Orders
live in standalone tables (app/models/catering_order.py), outside the POS.
"""
import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.core.clock import business_today
from app.core.database import get_db
from app.models.catering_order import CateringOrder, CateringOrderItem, CateringOrderStatus
from app.models.menu_item import MenuItem
from app.services import catering
from app.web.audit import log_change
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["catering"])


def _dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else default))
    except InvalidOperation:
        return Decimal(default)


def _load(db: Session, order_id: int) -> CateringOrder | None:
    return (db.query(CateringOrder).options(selectinload(CateringOrder.items))
            .filter(CateringOrder.id == order_id).first())


@router.get("/catering-orders", response_class=HTMLResponse)
def catering_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    data = catering.board(db)
    # ?new=1 -> blank order; ?id= -> that order; else the next upcoming one,
    # else a blank order.
    sel_id = request.query_params.get("id", "")
    order = None
    if request.query_params.get("new") != "1":
        if sel_id.isdigit():
            order = _load(db, int(sel_id))
        elif data["upcoming"]:
            order = _load(db, data["upcoming"][0].id)
    ctx = {"user": user, **data, "order": order, "is_new": order is None,
           "menu": catering.menu_options(db), "today": business_today(),
           "notice": request.query_params.get("notice")}
    if order:
        ctx["m"] = catering.money(order, catering.food_costs(db))
        ctx["ingredients"] = catering.ingredients(db, order)
    return _tmpl(request, "catering_orders.html", ctx)


@router.post("/catering-orders/save")
async def save_order(request: Request, db: Session = Depends(get_db)):
    """Create or update an order with its lines (JSON). Totals are computed
    here from the lines, never trusted from the page."""
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    b = await request.json()
    name, phone = (b.get("customer_name") or "").strip(), (b.get("customer_phone") or "").strip()
    if not name or not phone:
        return JSONResponse({"error": "Customer name and phone are needed."}, status_code=400)
    try:
        ddate = datetime.date.fromisoformat(b.get("delivery_date") or "")
        dtime = datetime.time.fromisoformat(b.get("delivery_time") or "12:00")
    except ValueError:
        return JSONResponse({"error": "Pick the event date and time."}, status_code=400)
    menu = {m.id: m for m in db.query(MenuItem).all()}
    lines = []
    for n, raw in enumerate(b.get("lines") or [], start=1):
        qty = int(_dec(raw.get("quantity")))
        rate = _dec(raw.get("rate"))
        mid = raw.get("menu_item_id")
        if qty <= 0 or rate < 0:
            return JSONResponse({"error": f"Line {n}: quantity and rate are needed."}, status_code=400)
        if mid:
            m = menu.get(int(mid))
            if not m:
                return JSONResponse({"error": f"Line {n}: that dish isn't on the menu."}, status_code=400)
            lines.append({"item_name": m.name, "menu_item_id": m.id, "menu_price": m.price, "quantity": qty,
                          "unit": "portions", "rate": rate, "amount": rate * qty})
        else:
            item = (raw.get("item_name") or "").strip()
            if not item:
                return JSONResponse({"error": f"Line {n}: name the custom item."}, status_code=400)
            lines.append({"item_name": item[:255], "menu_item_id": None, "menu_price": None, "quantity": qty,
                          "unit": (raw.get("unit") or "portions")[:50], "rate": rate, "amount": rate * qty})
    if not lines:
        return JSONResponse({"error": "Add at least one dish."}, status_code=400)

    oid = b.get("id")
    order = _load(db, int(oid)) if oid else None
    if oid and not order:
        return JSONResponse({"error": "That order no longer exists."}, status_code=404)
    if order is None:
        order = CateringOrder(order_taken_date=business_today(), status=CateringOrderStatus.quote, created_by=user.id,
                              advance_paid=Decimal(0), subtotal=Decimal(0), total_amount=Decimal(0), balance_due=Decimal(0))
        db.add(order)
    order.customer_name, order.customer_phone = name[:255], phone[:20]
    order.delivery_date, order.delivery_time = ddate, dtime
    order.delivery_address = (b.get("delivery_address") or "").strip() or None
    order.plates = int(_dec(b.get("plates"))) or None
    order.notes = (b.get("notes") or "").strip() or None
    order.items.clear()
    db.flush()
    for l in lines:
        order.items.append(CateringOrderItem(**l))
    # Line amounts at catering rates, less any extra order-level discount.
    total = sum((l["amount"] for l in lines), Decimal(0))
    total = max(total - _dec(b.get("extra_discount")), Decimal(0))
    at_menu = sum(((l["menu_price"] or l["rate"]) * l["quantity"] for l in lines), Decimal(0))
    order.subtotal, order.total_amount = at_menu, total
    order.discount = max(at_menu - total, Decimal(0))
    order.balance_due = total - (order.advance_paid or 0)
    order.payment_status = catering.payment_status(total, order.advance_paid or Decimal(0))
    db.commit()
    return JSONResponse({"ok": True, "id": order.id})


_NEXT = {"confirmed", "in_prep", "delivered", "cancelled", "quote"}


@router.post("/catering-orders/{order_id}/status")
async def set_status(order_id: int, request: Request, db: Session = Depends(get_db)):
    """{status, advance?}: move the order; confirming can record the advance."""
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    b = await request.json()
    order = _load(db, order_id)
    if not order or b.get("status") not in _NEXT:
        return JSONResponse({"error": "Not found"}, status_code=404)
    old = order.status.value
    order.status = CateringOrderStatus(b["status"])
    if b.get("advance") not in (None, ""):
        order.advance_paid = _dec(b["advance"])
    order.balance_due = order.total_amount - order.advance_paid
    order.payment_status = catering.payment_status(order.total_amount, order.advance_paid)
    log_change(db, batch="catering_status", target_table="catering_orders", target_id=order.id, field="status",
               old_value=old, new_value=order.status.value, reason="Changed on the Catering page", actor_user_id=user.id)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/catering-orders/{order_id}/payment")
async def record_payment(order_id: int, request: Request, db: Session = Depends(get_db)):
    """{amount}: money received now, added to what's been paid."""
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    b = await request.json()
    order = _load(db, order_id)
    amt = _dec(b.get("amount"))
    if not order or amt <= 0:
        return JSONResponse({"error": "Type the amount received."}, status_code=400)
    old = order.advance_paid
    order.advance_paid = (order.advance_paid or 0) + amt
    order.balance_due = order.total_amount - order.advance_paid
    order.payment_status = catering.payment_status(order.total_amount, order.advance_paid)
    log_change(db, batch="catering_payment", target_table="catering_orders", target_id=order.id, field="advance_paid",
               old_value=old, new_value=order.advance_paid, reason=f"Received ₹{amt}", actor_user_id=user.id)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/catering-orders/{order_id}/to-buy")
def to_buy(order_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    order = _load(db, order_id)
    if not order:
        return RedirectResponse("/catering-orders", status_code=303)
    added, skipped = catering.add_to_buy(db, order, user.id)
    db.commit()
    msg = f"{added} item{'s' if added != 1 else ''} added to Buy (Approved · waiting)."
    if skipped:
        msg += f" {skipped} already approved there."
    return RedirectResponse(f"/catering-orders?id={order.id}&notice={msg}", status_code=303)
