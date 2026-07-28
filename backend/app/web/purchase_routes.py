"""Purchase entry, listing, editing and soft deletion.

A purchase row is a financial record. Three rules hold everywhere below:

1. Nothing is ever hard-deleted. Deletion sets deleted_at / deleted_by /
   delete_reason. Every query that feeds a cost number filters deleted rows out.
2. No edit and no deletion happens without a reason and an actor written to
   cost_base_repair_log in the SAME transaction as the change.
3. Every change is followed by resync_derived_costs(), because menu_items
   carries a frozen cost snapshot that does not recompute on its own.
"""
from datetime import date
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.core.database import get_db
from app.models.ingredient import Ingredient
from app.models.purchase import Purchase
from app.web.audit import log_change, log_field_diffs, resync_derived_costs
from app.web.deps import _tmpl, require_user

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
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    ctx = {
        "user": user,
        "ingredients": _ingredient_options(db),
        "error": None,
        "today": date.today().isoformat(),
    }
    if usage_type not in _SELECTABLE_USAGE:
        ctx["error"] = f"Unknown usage type '{usage_type}'."
        return _tmpl(request, "purchases_new.html", ctx, status_code=400)
    try:
        p = Purchase(
            ingredient_id=ingredient_id,
            qty=float(qty),
            unit=unit,
            total_price=float(total_price),
            purchase_date=date.fromisoformat(purchase_date),
            usage_type=usage_type,
            entered_by_user_id=user.id,
            notes=notes.strip() or None,
        )
        db.add(p)
        db.flush()
        # A new purchase moves ingredient cost, so the menu snapshot is stale
        # from this moment until it is repointed.
        resync_derived_costs(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        ctx["error"] = f"Could not save: {exc}"
        return _tmpl(request, "purchases_new.html", ctx, status_code=400)
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
