"""Purchase entry, listing, editing and soft deletion.

A purchase row is a financial record. Three rules hold everywhere below:

1. Nothing is ever hard-deleted. Deletion sets deleted_at / deleted_by /
   delete_reason. Every query that feeds a cost number filters deleted rows out.
2. No edit and no deletion happens without a reason and an actor written to
   cost_base_repair_log in the SAME transaction as the change.
3. Every change is followed by resync_derived_costs(), because menu_items
   carries a frozen cost snapshot that does not recompute on its own.
"""
import re
from datetime import date, timedelta
import json
from urllib.parse import urlencode
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.core.database import get_db
from app.core.clock import business_today, business_tz
from app.models.ingredient import Ingredient
from app.models.purchase import Purchase
from app.models.user import User
from app.services.order_derived_stock import sync_order_derived_stock
from app.services import purchases_page
from app.web.audit import log_change, log_field_diffs, resync_derived_costs
from app.web.deps import _tmpl, require_user
import logging

logger = logging.getLogger("purchases")

router = APIRouter(tags=["purchases"])

# Values the edit form is allowed to set. excluded_unidentified exists in the
# database enum but is not an operator-selectable state; a row already holding
# it keeps it (see _usage_choices) rather than being silently reclassified.
_SELECTABLE_USAGE = ("menu", "others_personal")

MIN_DELETE_REASON = 10
MIN_EDIT_REASON = 5


def _live(q):
    """Restrict a Purchase query to rows that have not been soft-deleted."""
    return q.filter(Purchase.deleted_at.is_(None))


DUP_WINDOW_DAYS = 1


def _unit_str(unit) -> str:
    """unit arrives as a Python enum from the ORM and as a plain string from
    the form. Render both the same way."""
    return getattr(unit, "value", unit)


# Rates are shown in the base unit of their family so a gram row and a
# kilogram row can be read against each other on the same screen. Without
# this, Butter 28 Jul reads as Rs 0.60/g beside Rs 220.00/kg and the
# contradiction is invisible.
_RATE_BASE = {"g": ("kg", 1000.0), "ml": ("l", 1000.0)}


def _rate_text(qty, total_price, unit) -> str:
    """Price per base unit, or a dash when it cannot be computed.

    The rate is what exposes the mistakes a price match cannot see. Butter on
    28 Jul is the live example: 200 g for Rs 120 (Rs 600/kg) sitting next to
    2 kg for Rs 440 (Rs 220/kg). Same ingredient, same day, different price,
    and one of the two rows is wrong.
    """
    try:
        q = float(qty)
        if q <= 0:
            return "\u2014"
        u = _unit_str(unit)
        base, factor = _RATE_BASE.get(u, (u, 1.0))
        return "\u20b9{:,.2f}/{}".format((float(total_price) / q) * factor, base)
    except (TypeError, ValueError, ZeroDivisionError):
        return "\u2014"


def _duplicate_candidates(db: Session, ingredient_id: int, purchase_date: date, total_price: float):
    """Live purchases of the same ingredient that this entry may be repeating.

    Two windows, because they catch different mistakes:

    * same day, any price -- the same paper memo keyed twice by two people,
      and same-day rate contradictions where one of the two rows is wrong.
    * same total price, within one day either side -- the same memo entered on
      adjacent days. Cooking Gas ids 64 and 113 (Rs 3,400, 30 Jun and 1 Jul)
      is the live example.

    Measured against all 292 live rows on 2026-07-28, these two windows would
    have fired on 18 entries (6.2%, about one warning every day and a half at
    current entry volume), of which 6 look like genuine defects.

    It warns; it never blocks. Daily greens legitimately repeat -- coriander,
    curd, milk and lemon all recur at the same price on consecutive days -- so
    a hard block would be wrong most of the time it fired, and would push
    people into worse workarounds.
    """
    lo = purchase_date - timedelta(days=DUP_WINDOW_DAYS)
    hi = purchase_date + timedelta(days=DUP_WINDOW_DAYS)
    rows = (
        _live(db.query(Purchase))
        .filter(Purchase.ingredient_id == ingredient_id)
        .filter(Purchase.purchase_date.between(lo, hi))
        .order_by(Purchase.purchase_date.desc(), Purchase.id.desc())
        .all()
    )
    out = []
    for r in rows:
        same_day = r.purchase_date == purchase_date
        # Rounded to the paisa: total_price is NUMERIC and float() round-trips
        # can differ in the last bit.
        price_equal = abs(float(r.total_price) - float(total_price)) < 0.005
        if not (same_day or price_equal):
            continue
        gap = abs((r.purchase_date - purchase_date).days)
        if same_day and price_equal:
            why = "same day, same amount"
        elif same_day:
            why = "same day, different amount"
        else:
            why = "same amount, {} day{} apart".format(gap, "" if gap == 1 else "s")
        out.append({
            "id": r.id,
            "qty": r.qty,
            "unit": _unit_str(r.unit),
            "total_price": r.total_price,
            "purchase_date": r.purchase_date,
            "rate": _rate_text(r.qty, r.total_price, r.unit),
            "entered_by": r.entered_by_user_id,
            "notes": r.notes,
            "why": why,
        })
    return out



def _ingredient_options(db: Session, purchase: Purchase | None = None):
    """Active ingredients, plus this row's own ingredient even if deactivated."""
    ingredients = (
        db.query(Ingredient).filter(Ingredient.is_active.is_(True)).order_by(Ingredient.name).all()
    )
    if purchase is not None and purchase.ingredient_id not in {i.id for i in ingredients}:
        current = db.get(Ingredient, purchase.ingredient_id)
        if current:
            ingredients = sorted([*ingredients, current], key=lambda i: i.name)
    return ingredients


def _snapshot(p: Purchase) -> dict:
    """The fields of a purchase that carry financial meaning."""
    return {
        "ingredient_id": p.ingredient_id,
        "qty": p.qty,
        "unit": p.unit,
        "total_price": p.total_price,
        "purchase_date": p.purchase_date,
        "usage_type": p.usage_type,
        "notes": p.notes,
    }


@router.get("/purchases", response_class=HTMLResponse)
def purchases_list(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    raw_id = request.query_params.get("ingredient_id", "")
    filter_id = int(raw_id) if raw_id.isdigit() else None
    show_deleted = bool(request.query_params.get("show_deleted"))
    if not filter_id and not show_deleted:
        return _overview(request, db, user)

    q = db.query(Purchase).order_by(Purchase.purchase_date.desc(), Purchase.created_at.desc())
    q = q.filter(Purchase.deleted_at.isnot(None)) if show_deleted else _live(q)
    if filter_id:
        q = q.filter(Purchase.ingredient_id == filter_id)
    purchases = q.limit(200).all()

    deleted_count = _count_deleted(db, filter_id)
    ingredients = db.query(Ingredient).order_by(Ingredient.name).all()
    return _tmpl(request, "purchases_list.html", {
        "user": user,
        "purchases": purchases,
        "ingredients": ingredients,
        "filter_id": filter_id,
        "show_deleted": show_deleted,
        "deleted_count": deleted_count,
        "min_delete_reason": MIN_DELETE_REASON,
        "error": request.query_params.get("error"),
        "notice": request.query_params.get("notice"),
    })


def _overview(request: Request, db: Session, user):
    """The Purchases page: new bill, need fixing, price watch, recent bills."""
    usual = purchases_page.usual_prices(db)
    items = [{"id": i.id, "name": i.name, "unit": _unit_str(i.unit), "pack_g": float(i.pack_size_g) if i.pack_size_g else None,
              "usual": round(usual[i.id]["price"], 4) if i.id in usual else None}
             for i in _ingredient_options(db)]
    sel = request.query_params.get("sel")
    return _tmpl(request, "purchases.html", {
        "user": user,
        "s": purchases_page.summary(db),
        "fix": purchases_page.need_fixing(db),
        "watch": purchases_page.price_watch(usual),
        "days": purchases_page.recent_bills(db),
        "vendors": purchases_page.vendors(db),
        "items_json": json.dumps(items),
        "today": business_today().isoformat(),
        "sel": int(sel) if sel and sel.isdigit() else None,
        "deleted_count": _count_deleted(db, None),
        "min_delete_reason": MIN_DELETE_REASON,
        "error": request.query_params.get("error"),
        "notice": request.query_params.get("notice"),
        "add_error": request.query_params.get("add_error"),
    })


def _count_deleted(db: Session, filter_id: int | None) -> int:
    q = db.query(func.count(Purchase.id)).filter(Purchase.deleted_at.isnot(None))
    if filter_id:
        q = q.filter(Purchase.ingredient_id == filter_id)
    return int(q.scalar() or 0)


@router.get("/purchases/new")
def purchases_new_get(request: Request):
    """Entry moved onto /purchases (one bill, many lines). Quick-add still
    lands here with ?sel= / ?add_error=, which the new page picks up."""
    keep = {k: v for k, v in request.query_params.items() if k in ("sel", "add_error")}
    return RedirectResponse("/purchases" + ("?" + urlencode(keep) if keep else "") + "#new-bill", status_code=303)


_UNITS = ("kg", "g", "l", "ml", "pcs")


@router.post("/purchases/bill")
async def save_bill(request: Request, db: Session = Depends(get_db)):
    """Save a whole bill: {date, vendor, bill_ref, usage_type, override,
    lines: [{ingredient_id, qty, unit, amount}]}. Lines arrive in the item's
    own unit (the page converts). Possible duplicates come back as 409 with
    the list unless override is set; an override is audit-logged."""
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    body = await request.json()
    try:
        pdate = date.fromisoformat(body.get("date") or "")
    except ValueError:
        return JSONResponse({"error": "Pick the bill date."}, status_code=400)
    if pdate > business_today() + timedelta(days=1):
        return JSONResponse({"error": "The bill date is in the future."}, status_code=400)
    usage = body.get("usage_type") or "menu"
    if usage not in _SELECTABLE_USAGE:
        return JSONResponse({"error": "Unknown usage type."}, status_code=400)
    vendor = (body.get("vendor") or "").strip()[:120] or None
    bill_ref = (body.get("bill_ref") or "").strip()[:80] or None
    lines = []
    for n, raw in enumerate(body.get("lines") or [], start=1):
        try:
            iid, qty, amount = int(raw["ingredient_id"]), float(raw["qty"]), float(raw["amount"])
        except (KeyError, TypeError, ValueError):
            return JSONResponse({"error": f"Line {n}: item, quantity and amount are all needed."}, status_code=400)
        unit = raw.get("unit")
        if qty <= 0 or amount < 0 or unit not in _UNITS or not db.get(Ingredient, iid):
            return JSONResponse({"error": f"Line {n}: check the quantity, unit and amount."}, status_code=400)
        lines.append((iid, qty, unit, amount))
    if not lines:
        return JSONResponse({"error": "Add at least one line."}, status_code=400)

    dups = []
    matched_lines = 0
    for iid, qty, unit, amount in lines:
        found = _duplicate_candidates(db, iid, pdate, amount)
        matched_lines += bool(found)
        for d in found:
            ing = db.get(Ingredient, iid)
            who = db.get(User, d["entered_by"]) if d["entered_by"] else None
            prev = db.get(Purchase, d["id"])
            when = prev.created_at.astimezone(business_tz()).strftime("%d %b %I:%M %p") if prev and prev.created_at else ""
            by = f" \u00b7 entered by {who.name} {when}" if who else ""
            dups.append(f"{ing.name}: {d['qty']} {d['unit']} for \u20b9{float(d['total_price']):,.0f} "
                        f"on {d['purchase_date']:%d %b} ({d['why']}){by}")
    if dups and not body.get("override"):
        # Every line already matches a saved purchase: almost certainly the
        # same memo keyed twice (two people logged one cash memo on 10 Oct).
        return JSONResponse({"duplicates": dups, "whole_bill": matched_lines == len(lines)}, status_code=409)

    note = " \u00b7 ".join(x for x in (vendor, bill_ref) if x) or None
    created = []
    try:
        for iid, qty, unit, amount in lines:
            p = Purchase(ingredient_id=iid, qty=qty, unit=unit, total_price=amount, purchase_date=pdate,
                         usage_type=usage, entered_by_user_id=user.id, vendor=vendor, bill_ref=bill_ref, notes=note)
            db.add(p)
            db.flush()
            created.append(p.id)
        if dups:
            log_change(db, batch="purchase_duplicate_override", target_table="purchases", target_id=created[0],
                       field="duplicate_warning_overridden", old_value=None, new_value="; ".join(dups)[:2000],
                       reason=f"Operator confirmed the bill is new despite {len(dups)} similar row(s).",
                       actor_user_id=user.id)
        resync_derived_costs(db)
        try:
            with db.begin_nested():
                sync_order_derived_stock(db)
        except Exception:
            logger.exception("order-derived sync failed")
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("bill save failed")
        return JSONResponse({"error": f"Could not save: {exc}"}, status_code=500)
    return JSONResponse({"ok": True, "created": len(created)})


@router.post("/purchases/fix-per-piece")
async def fix_per_piece(request: Request, db: Session = Depends(get_db)):
    """Convert an item's piece-logged rows to its weight unit with one weight
    per piece (e.g. one Gobi head = 0.8 kg). Audit-logged per row."""
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    body = await request.json()
    try:
        iid, per = int(body["ingredient_id"]), float(body["kg_per_piece"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "Type the weight of one piece in kg."}, status_code=400)
    ing = db.get(Ingredient, iid)
    if not ing or not (0 < per <= 25):
        return JSONResponse({"error": "That weight doesn't look right."}, status_code=400)
    unit = _unit_str(ing.unit)
    if unit not in ("kg", "g"):
        return JSONResponse({"error": f"{ing.name} isn't tracked by weight."}, status_code=400)
    rows = _live(db.query(Purchase)).filter(Purchase.ingredient_id == iid, Purchase.unit == "pcs").all()
    for p in rows:
        new_qty = float(p.qty) * per * (1000 if unit == "g" else 1)
        log_change(db, batch="purchase_unit_fix", target_table="purchases", target_id=p.id, field="qty/unit",
                   old_value=f"{p.qty} pcs", new_value=f"{new_qty:g} {unit}",
                   reason=f"{ing.name} pieces converted at {per:g} kg per piece", actor_user_id=user.id)
        p.qty, p.unit = new_qty, unit
    db.flush()
    resync_derived_costs(db)
    db.commit()
    return JSONResponse({"ok": True, "fixed": len(rows)})


@router.get("/purchases/{purchase_id}/edit", response_class=HTMLResponse)
def purchases_edit_get(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    purchase = db.get(Purchase, purchase_id)
    if not purchase:
        return RedirectResponse("/purchases", status_code=302)
    if purchase.deleted_at is not None:
        return RedirectResponse(
            "/purchases?show_deleted=1&error=That+purchase+is+deleted+and+cannot+be+edited.",
            status_code=302,
        )
    return _tmpl(request, "purchases_edit.html", {
        "user": user,
        "purchase": purchase,
        "ingredients": _ingredient_options(db, purchase),
        "selectable_usage": _SELECTABLE_USAGE,
        "min_edit_reason": MIN_EDIT_REASON,
        "error": None,
    })


@router.post("/purchases/{purchase_id}/edit")
async def purchases_edit_post(
    purchase_id: int,
    request: Request,
    ingredient_id: int = Form(...),
    qty: str = Form(...),
    unit: str = Form(...),
    total_price: str = Form(...),
    purchase_date: str = Form(...),
    usage_type: str = Form(...),
    reason: str = Form(default=""),
    notes: str = Form(default=""),
    row_version: int = Form(...),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    purchase = db.get(Purchase, purchase_id)
    if not purchase:
        return RedirectResponse("/purchases", status_code=302)
    if purchase.deleted_at is not None:
        return RedirectResponse(
            "/purchases?show_deleted=1&error=That+purchase+is+deleted+and+cannot+be+edited.",
            status_code=302,
        )

    def fail(message: str):
        db.rollback()
        return _tmpl(request, "purchases_edit.html", {
            "user": user,
            "purchase": db.get(Purchase, purchase_id),
            "ingredients": _ingredient_options(db, purchase),
            "selectable_usage": _SELECTABLE_USAGE,
            "min_edit_reason": MIN_EDIT_REASON,
            "error": message,
        }, status_code=400)

    reason = reason.strip()
    if len(reason) < MIN_EDIT_REASON:
        return fail(
            f"Give a reason for the change ({MIN_EDIT_REASON} characters or more). "
            "It is written to the audit log against your name."
        )
    # A row may already hold a usage state the form does not offer. Keeping it
    # is allowed; switching to an unknown one is not.
    if usage_type not in _SELECTABLE_USAGE and usage_type != purchase.usage_type:
        return fail(f"Unknown usage type '{usage_type}'.")
    # Somebody else saved this row after the form was opened.
    if row_version != purchase.row_version:
        return fail(
            "Somebody else changed this purchase while you had it open. "
            "Your change was NOT saved. The figures shown are now the current "
            "ones — check them and re-apply your correction if it still applies."
        )

    before = _snapshot(purchase)
    try:
        purchase.ingredient_id = ingredient_id
        purchase.qty = float(qty)
        purchase.unit = unit
        purchase.total_price = float(total_price)
        purchase.purchase_date = date.fromisoformat(purchase_date)
        purchase.usage_type = usage_type
        purchase.notes = notes.strip() or None
        db.flush()

        changed = log_field_diffs(
            db,
            batch="purchase_edit",
            target_table="purchases",
            target_id=purchase.id,
            before=before,
            after=_snapshot(purchase),
            reason=reason,
            actor_user_id=user.id,
        )
        moved = resync_derived_costs(db) if changed else 0
        db.commit()
    except StaleDataError:
        return fail(
            "Somebody else saved this purchase a moment before you did. "
            "Your change was NOT saved. Re-open the row and check it."
        )
    except Exception as exc:
        return fail(f"Could not save: {exc}")

    if not changed:
        return RedirectResponse("/purchases?notice=Nothing+changed.", status_code=303)
    return RedirectResponse(
        f"/purchases?notice=Saved.+{len(changed)}+field(s)+changed%2C+{moved}+menu+cost(s)+updated.",
        status_code=303,
    )


@router.post("/purchases/{purchase_id}/delete")
async def purchases_delete_post(
    purchase_id: int,
    request: Request,
    reason: str = Form(default=""),
    row_version: int = Form(...),
    db: Session = Depends(get_db),
):
    """Soft delete. The row stays in the table; it stops counting."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    purchase = db.get(Purchase, purchase_id)
    if not purchase:
        return RedirectResponse("/purchases?error=That+purchase+no+longer+exists.", status_code=303)
    if purchase.deleted_at is not None:
        return RedirectResponse("/purchases?notice=Already+deleted.", status_code=303)

    reason = reason.strip()
    if len(reason) < MIN_DELETE_REASON:
        return RedirectResponse(
            f"/purchases?error=Deleting+a+purchase+needs+a+reason+of+at+least+"
            f"{MIN_DELETE_REASON}+characters.+Nothing+was+deleted.",
            status_code=303,
        )
    if row_version != purchase.row_version:
        return RedirectResponse(
            "/purchases?error=That+row+changed+while+the+page+was+open.+"
            "Nothing+was+deleted.+Reload+and+look+at+it+again.",
            status_code=303,
        )

    # The whole row goes into the log BEFORE it stops counting, so the deleted
    # figures remain recoverable from the audit trail alone.
    snapshot = _snapshot(purchase)
    snapshot["entered_by_user_id"] = purchase.entered_by_user_id
    rendered = "; ".join(f"{k}={v}" for k, v in snapshot.items())

    try:
        log_change(
            db,
            batch="purchase_soft_delete",
            target_table="purchases",
            target_id=purchase.id,
            field=None,
            old_value=rendered,
            new_value="deleted",
            reason=reason,
            actor_user_id=user.id,
        )
        purchase.deleted_at = func.now()
        purchase.deleted_by = user.id
        purchase.delete_reason = reason
        db.flush()
        moved = resync_derived_costs(db)
        db.commit()
    except StaleDataError:
        db.rollback()
        return RedirectResponse(
            "/purchases?error=Somebody+else+changed+that+row+as+you+deleted+it.+"
            "Nothing+was+deleted.",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        return RedirectResponse(f"/purchases?error=Could+not+delete:+{exc}", status_code=303)

    return RedirectResponse(
        f"/purchases?notice=Purchase+deleted.+{moved}+menu+cost(s)+updated.", status_code=303
    )


@router.post("/purchases/{purchase_id}/restore")
async def purchases_restore_post(
    purchase_id: int,
    request: Request,
    row_version: int = Form(...),
    db: Session = Depends(get_db),
):
    """Undo a soft delete. Deleting the wrong row must not be a one-way door."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    purchase = db.get(Purchase, purchase_id)
    if not purchase or purchase.deleted_at is None:
        return RedirectResponse("/purchases?show_deleted=1", status_code=303)
    if row_version != purchase.row_version:
        return RedirectResponse(
            "/purchases?show_deleted=1&error=That+row+changed+while+the+page+was+open.+"
            "Nothing+was+restored.",
            status_code=303,
        )
    try:
        log_change(
            db,
            batch="purchase_restore",
            target_table="purchases",
            target_id=purchase.id,
            field=None,
            old_value=f"deleted: {purchase.delete_reason}",
            new_value="restored",
            reason="Soft delete reversed.",
            actor_user_id=user.id,
        )
        purchase.deleted_at = None
        purchase.deleted_by = None
        purchase.delete_reason = None
        db.flush()
        moved = resync_derived_costs(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        return RedirectResponse(
            f"/purchases?show_deleted=1&error=Could+not+restore:+{exc}", status_code=303
        )
    return RedirectResponse(
        f"/purchases?notice=Purchase+restored.+{moved}+menu+cost(s)+updated.", status_code=303
    )


# ---------------------------------------------------------------------------
# Receipt upload -> OCR -> review -> bulk create
# ---------------------------------------------------------------------------
def _snake(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "user"


def _receipt_filename(db: Session, user, original: str | None) -> str:
    """snake_case(uploader)_YYYY_MM_DD[.n].ext, unique within purchase_receipts."""
    uploader = (getattr(user, "name", None) or getattr(user, "username", None)
                or getattr(user, "email", None) or f"user{user.id}")
    ext = ""
    if original and "." in original:
        ext = "." + re.sub(r"[^a-z0-9]", "", original.rsplit(".", 1)[1].lower())[:5]
    base = f"{_snake(uploader)}_{business_today().isoformat().replace('-', '_')}"
    name, n = f"{base}{ext}", 2
    while db.execute(text("SELECT 1 FROM purchase_receipts WHERE stored_filename = :n"),
                     {"n": name}).first():
        name, n = f"{base}_{n}{ext}", n + 1
    return name
