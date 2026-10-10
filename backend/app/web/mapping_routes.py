"""Recipes page (was Ingredient Mapping) and ingredient create routes."""
from fastapi import APIRouter, Request, Form, Depends
import json
import logging
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.ingredient import Ingredient
from app.services import recipes_page
from app.web.deps import _tmpl, require_user

log = logging.getLogger("recipes")

router = APIRouter(tags=["mapping"])


@router.get("/ingredients/mapping", response_class=HTMLResponse)
def recipes(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    d = recipes_page.data(db)
    q = request.query_params
    return _tmpl(request, "recipes.html", {
        "user": user,
        "data_json": json.dumps(d),
        "sel": {"dish": q.get("dish"), "ing": q.get("ing"), "tab": q.get("tab")},
        "add_error": q.get("err"),
    })


@router.get("/ingredients/mapping/{ingredient_id}")
def mapping_detail(ingredient_id: int):
    return RedirectResponse(f"/ingredients/mapping?tab=ing&ing={ingredient_id}", status_code=301)


async def _json(request: Request, db: Session):
    user, redir = require_user(request, db)
    if redir:
        return None, None
    return user, await request.json()


@router.post("/recipes/dish/{menu_item_id}")
async def recipes_save_dish(menu_item_id: int, request: Request, db: Session = Depends(get_db)):
    user, body = await _json(request, db)
    if user is None:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    try:
        recipes_page.save_dish(db, menu_item_id, body.get("changes") or [], body.get("adds") or [], body.get("removes") or [])
    except (ValueError, KeyError, TypeError) as exc:
        db.rollback()
        return JSONResponse({"error": str(exc) or "Not saved"}, status_code=400)
    log.info("recipe saved by %s: dish %s %s", user.username, menu_item_id, body)
    return JSONResponse({"ok": True})


@router.post("/recipes/dish/{menu_item_id}/confirm")
async def recipes_confirm_dish(menu_item_id: int, request: Request, db: Session = Depends(get_db)):
    user, _ = await _json(request, db)
    if user is None:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    n = recipes_page.confirm_dish(db, menu_item_id)
    log.info("recipe confirmed by %s: dish %s (%s lines)", user.username, menu_item_id, n)
    return JSONResponse({"ok": True, "confirmed": n})


@router.post("/recipes/ingredient/{ingredient_id}/role")
async def recipes_set_role(ingredient_id: int, request: Request, db: Session = Depends(get_db)):
    user, body = await _json(request, db)
    if user is None:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    try:
        recipes_page.set_role(db, ingredient_id, body.get("role"))
    except ValueError as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=400)
    log.info("ingredient role by %s: %s -> %s", user.username, ingredient_id, body.get("role"))
    return JSONResponse({"ok": True})


@router.post("/ingredients/quick-add")
async def ingredient_quick_add(
    request: Request,
    name: str = Form(...),
    unit: str = Form(...),
    category: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Create an ingredient from the purchases form and return to it with the new item pre-selected."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    name = name.strip()
    existing = db.query(Ingredient).filter(Ingredient.name == name).first()
    if existing:
        return RedirectResponse(
            f"/purchases/new?add_error={name}+already+exists&add_open=1&sel={existing.id}",
            status_code=303,
        )
    ing = Ingredient(name=name, unit=unit, category=category.strip() or None, is_active=True)
    db.add(ing)
    db.commit()
    db.refresh(ing)
    return RedirectResponse(f"/purchases/new?sel={ing.id}", status_code=303)


@router.post("/ingredients/new")
async def ingredient_new(
    request: Request,
    name: str = Form(...),
    unit: str = Form(...),
    category: str = Form(default=""),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    name = name.strip()
    if not name:
        return RedirectResponse("/ingredients/mapping?err=Name+cannot+be+empty", status_code=303)
    existing = db.query(Ingredient).filter(Ingredient.name == name).first()
    if existing:
        return RedirectResponse(f"/ingredients/mapping?err='{name}'+already+exists", status_code=303)
    ing = Ingredient(
        name=name,
        unit=unit,
        category=category.strip() or None,
        is_active=True,
    )
    db.add(ing)
    db.commit()
    db.refresh(ing)
    return RedirectResponse(f"/ingredients/mapping?tab=ing&ing={ing.id}", status_code=303)
