"""Purchase entry and listing routes."""
from datetime import date
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.ingredient import Ingredient
from app.models.purchase import Purchase
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["purchases"])


@router.get("/purchases", response_class=HTMLResponse)
def purchases_list(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    purchases = (
        db.query(Purchase)
        .order_by(Purchase.purchase_date.desc(), Purchase.created_at.desc())
        .limit(200)
        .all()
    )
    return _tmpl(request, "purchases_list.html", {"user": user, "purchases": purchases})


@router.get("/purchases/new", response_class=HTMLResponse)
def purchases_new_get(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    ingredients = db.query(Ingredient).filter(Ingredient.is_active.is_(True)).order_by(Ingredient.name).all()
    sel = request.query_params.get("sel")
    return _tmpl(request, "purchases_new.html", {
        "user": user,
        "ingredients": ingredients,
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
    ingredients = db.query(Ingredient).filter(Ingredient.is_active.is_(True)).order_by(Ingredient.name).all()
    ctx = {"user": user, "ingredients": ingredients, "error": None, "today": date.today().isoformat()}
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
    ingredients = db.query(Ingredient).filter(Ingredient.is_active.is_(True)).order_by(Ingredient.name).all()
    # include the current ingredient even if it was deactivated
    current_ids = {i.id for i in ingredients}
    if purchase.ingredient_id not in current_ids:
        current_ing = db.get(Ingredient, purchase.ingredient_id)
        if current_ing:
            ingredients = sorted([*ingredients, current_ing], key=lambda i: i.name)
    return _tmpl(request, "purchases_edit.html", {
        "user": user,
        "purchase": purchase,
        "ingredients": ingredients,
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
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    purchase = db.get(Purchase, purchase_id)
    if not purchase:
        return RedirectResponse("/purchases", status_code=302)
    ingredients = db.query(Ingredient).filter(Ingredient.is_active.is_(True)).order_by(Ingredient.name).all()
    try:
        purchase.ingredient_id = ingredient_id
        purchase.qty = float(qty)
        purchase.unit = unit
        purchase.total_price = float(total_price)
        purchase.purchase_date = date.fromisoformat(purchase_date)
        purchase.usage_type = usage_type
        purchase.notes = notes.strip() or None
        db.commit()
    except Exception as exc:
        db.rollback()
        return _tmpl(request, "purchases_edit.html", {
            "user": user,
            "purchase": purchase,
            "ingredients": ingredients,
            "error": f"Could not save: {exc}",
        }, status_code=400)
    return RedirectResponse("/purchases", status_code=303)
