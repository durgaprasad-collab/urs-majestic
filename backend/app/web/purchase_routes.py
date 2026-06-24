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
