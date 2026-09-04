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
from fastapi import APIRouter, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.core.database import get_db
from app.core.clock import business_today
from app.models.ingredient import Ingredient
from app.models.purchase import Purchase
from app.services import gdrive
from app.services.order_derived_stock import sync_order_derived_stock
from app.services.receipt_parse import ocr_image, parse_receipt_text
from app.web.audit import log_change, log_field_diffs, resync_derived_costs
from app.web.deps import _tmpl, require_user
import logging

logger = logging.getLogger("purchases")

# Reject non-images and oversized uploads before OCR.
_MAX_RECEIPT_BYTES = 10 * 1024 * 1024  # 10 MB

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


def _count_deleted(db: Session, filter_id: int | None) -> int:
    q = db.query(func.count(Purchase.id)).filter(Purchase.deleted_at.isnot(None))
    if filter_id:
        q = q.filter(Purchase.ingredient_id == filter_id)
    return int(q.scalar() or 0)


@router.get("/purchases/new", response_class=HTMLResponse)
def purchases_new_get(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    sel = request.query_params.get("sel")
    return _tmpl(request, "purchases_new.html", {
        "user": user,
        "ingredients": _ingredient_options(db),
        "error": None,
        "today": date.today().isoformat(),
        "new_ingredient_id": int(sel) if sel and sel.isdigit() else None,
        "add_error": request.query_params.get("add_error"),
        "add_open": bool(request.query_params.get("add_open")),
    })


@router.post("/purchases/new")
async def purchases_new_post(
    request: Request,
    ingredient_id: int = Form(...),
    qty: str = Form(...),
    unit: str = Form(...),
    total_price: str = Form(...),
    purchase_date: str = Form(...),
    usage_type: str = Form(...),
    notes: str = Form(default=""),
    override_duplicate: str = Form(default=""),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    entered = {
        "ingredient_id": ingredient_id,
        "qty": qty,
        "unit": unit,
        "total_price": total_price,
        "purchase_date": purchase_date,
        "usage_type": usage_type,
        "notes": notes,
    }

    def render_form(message, duplicates=None, status: int = 400):
        """Re-render the form with everything the operator typed still in it.

        The previous version dropped every field on any failure and made them
        start again -- which is exactly the pressure that produces a careless
        re-entry.
        """
        return _tmpl(request, "purchases_new.html", {
            "user": user,
            "ingredients": _ingredient_options(db),
            "error": message,
            "today": date.today().isoformat(),
            "form": entered,
            "new_ingredient_id": ingredient_id,
            "duplicates": duplicates or [],
        }, status_code=status)

    if usage_type not in _SELECTABLE_USAGE:
        return render_form(f"Unknown usage type '{usage_type}'.")

    try:
        qty_f = float(qty)
        price_f = float(total_price)
        pdate = date.fromisoformat(purchase_date)
    except ValueError as exc:
        return render_form(f"Could not read what you entered: {exc}")

    # Computed even when the operator is overriding, so the audit log can
    # record what was overridden rather than just that something was.
    duplicates = _duplicate_candidates(db, ingredient_id, pdate, price_f)
    if duplicates and not override_duplicate:
        return render_form(None, duplicates=duplicates, status=200)

    try:
        p = Purchase(
            ingredient_id=ingredient_id,
            qty=qty_f,
            unit=unit,
            total_price=price_f,
            purchase_date=pdate,
            usage_type=usage_type,
            entered_by_user_id=user.id,
            notes=notes.strip() or None,
        )
        db.add(p)
        db.flush()
        if duplicates:
            # An override is a judgement call on a financial record, so it is
            # logged like every other one. This also makes the override rate
            # measurable: if it runs near 100%, the warning is noise and the
            # windows need narrowing.
            log_change(
                db,
                batch="purchase_duplicate_override",
                target_table="purchases",
                target_id=p.id,
                field="duplicate_warning_overridden",
                old_value=None,
                new_value="; ".join(
                    "#{} ({})".format(d["id"], d["why"]) for d in duplicates
                ),
                reason=(
                    "Operator confirmed this is a separate purchase despite "
                    "{} nearby row(s) for the same ingredient.".format(len(duplicates))
                ),
                actor_user_id=user.id,
            )
        # A new purchase moves ingredient cost, so the menu snapshot is stale
        # from this moment until it is repointed.
        resync_derived_costs(db)
        try:
            with db.begin_nested():
                sync_order_derived_stock(db)
        except Exception:
            # Experimental comparison model -- must never block a purchase save.
            logger.exception("order-derived sync failed")
        db.commit()
    except Exception as exc:
        db.rollback()
        return render_form(f"Could not save: {exc}")
    return RedirectResponse("/purchases", status_code=303)


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


@router.post("/purchases/upload-receipt")
async def upload_receipt(request: Request, file: UploadFile = File(...),
                         db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    def back(msg):
        return _tmpl(request, "purchases_new.html", {
            "user": user, "ingredients": _ingredient_options(db), "error": msg,
            "today": date.today().isoformat(), "new_ingredient_id": None,
        }, status_code=400)

    data = await file.read()
    ctype = file.content_type or ""
    if not ctype.startswith("image/"):
        return back("Please upload an image of the receipt (JPG or PNG).")
    if len(data) > _MAX_RECEIPT_BYTES:
        return back("That image is too large -- please keep it under 10 MB.")

    try:
        ocr_text = ocr_image(data, ctype)
    except Exception:
        return back("Could not read the image right now -- please try again, or "
                    "enter the purchase manually below.")

    ing_map = {i.name.lower(): i.id for i in _ingredient_options(db)}
    lines = parse_receipt_text(ocr_text, ing_map)

    # Archive the image (best-effort) and record the receipt.
    stored = _receipt_filename(db, user, file.filename)
    file_id, link = gdrive.upload_receipt(data, stored, ctype)
    rid = db.execute(text(
        "INSERT INTO purchase_receipts (drive_file_id, drive_link, original_filename, "
        " stored_filename, content_type, ocr_text, uploaded_by) "
        "VALUES (:fid, :link, :orig, :stored, :ct, :ocr, :uid) RETURNING id"
    ), {"fid": file_id, "link": link, "orig": file.filename, "stored": stored,
        "ct": ctype, "ocr": ocr_text, "uid": user.id}).scalar()
    db.commit()

    return _tmpl(request, "purchases_receipt_review.html", {
        "user": user,
        "ingredients": _ingredient_options(db),
        "lines": lines,
        "receipt_id": rid,
        "drive_link": link,
        "archived": bool(file_id),
        "today": business_today().isoformat(),
    })


@router.post("/purchases/receipt-confirm")
async def receipt_confirm(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    form = await request.form()
    receipt_id = int(form["receipt_id"]) if form.get("receipt_id", "").isdigit() else None
    try:
        n = int(form.get("row_count", "0"))
    except ValueError:
        n = 0

    created = skipped = 0
    for i in range(n):
        if form.get(f"include_{i}") != "1":
            continue
        try:
            ingredient_id = int(form.get(f"ingredient_id_{i}", ""))
            qty = float(form.get(f"qty_{i}", ""))
            unit = form.get(f"unit_{i}", "")
            price = float(form.get(f"total_price_{i}", ""))
            pdate = date.fromisoformat(form.get(f"purchase_date_{i}", ""))
            usage = form.get(f"usage_type_{i}", "menu")
        except (TypeError, ValueError):
            skipped += 1
            continue
        if (usage not in _SELECTABLE_USAGE or ingredient_id <= 0
                or unit not in ("kg", "g", "l", "ml", "pcs")):
            skipped += 1
            continue
        db.add(Purchase(
            ingredient_id=ingredient_id, qty=qty, unit=unit, total_price=price,
            purchase_date=pdate, usage_type=usage, entered_by_user_id=user.id,
            purchase_receipt_id=receipt_id, notes="From uploaded receipt",
        ))
        created += 1

    if created:
        db.flush()
        resync_derived_costs(db)  # single cost engine; caller commits
        try:
            with db.begin_nested():
                sync_order_derived_stock(db)
        except Exception:
            logger.exception("order-derived sync failed")
        db.commit()
    msg = f"Created+{created}+purchase(s)+from+the+receipt."
    if skipped:
        msg += f"+{skipped}+row(s)+skipped."
    return RedirectResponse(f"/purchases?notice={msg}", status_code=303)
