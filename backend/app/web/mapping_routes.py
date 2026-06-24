"""Ingredient-to-dish mapping routes."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.ingredient import Ingredient, IngredientDishMap
from app.models.menu_item import MenuItem
from app.models.item_sale import ItemSale
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["mapping"])


@router.get("/ingredients/mapping", response_class=HTMLResponse)
def mapping_list(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    ingredients = db.query(Ingredient).filter(Ingredient.is_active.is_(True)).order_by(Ingredient.name).all()
    counts_rows = (
        db.query(IngredientDishMap.ingredient_id, func.count(IngredientDishMap.id))
        .group_by(IngredientDishMap.ingredient_id)
        .all()
    )
    map_counts = {row[0]: row[1] for row in counts_rows}
    return _tmpl(request, "mapping_list.html", {
        "user": user,
        "ingredients": ingredients,
        "map_counts": map_counts,
        "add_error": request.query_params.get("err"),
    })


@router.get("/ingredients/mapping/{ingredient_id}", response_class=HTMLResponse)
def mapping_detail(
    ingredient_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    ingredient = db.query(Ingredient).filter(Ingredient.id == ingredient_id).first()
    if not ingredient:
        return RedirectResponse("/ingredients/mapping", status_code=302)

    # Top 20 dishes by revenue; fall back to all active food items if no sales
    sales_agg = (
        db.query(ItemSale.item_name, func.sum(ItemSale.revenue).label("rev"))
        .group_by(ItemSale.item_name)
        .order_by(func.sum(ItemSale.revenue).desc())
        .all()
    )
    name_to_rev = {r.item_name: float(r.rev) for r in sales_agg}
    all_items = (
        db.query(MenuItem)
        .filter(MenuItem.is_food.is_(True), MenuItem.is_active.is_(True))
        .all()
    )
    # Sort by category then revenue desc — Jinja2 groupby requires adjacent groups
    all_items_sorted = sorted(
        all_items,
        key=lambda i: (i.category or "", -name_to_rev.get(i.name, 0)),
    )

    # Existing mappings for this ingredient
    existing = (
        db.query(IngredientDishMap)
        .filter(IngredientDishMap.ingredient_id == ingredient_id)
        .all()
    )
    mapped_intensity = {m.menu_item_id: m.intensity for m in existing}

    dishes = [
        {
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "revenue": name_to_rev.get(item.name, 0),
            "is_mapped": item.id in mapped_intensity,
            "intensity": mapped_intensity.get(item.id, "medium"),
        }
        for item in all_items_sorted
    ]

    saved = request.query_params.get("saved") == "1"
    return _tmpl(request, "mapping_detail.html", {
        "user": user,
        "ingredient": ingredient,
        "dishes": dishes,
        "saved": saved,
    })


@router.post("/ingredients/mapping/{ingredient_id}")
async def mapping_save(
    ingredient_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    form = await request.form()
    # Delete all current mappings for this ingredient
    db.query(IngredientDishMap).filter(IngredientDishMap.ingredient_id == ingredient_id).delete()

    if form.get("apply_all") == "1":
        # Map to every active food item at the chosen intensity
        intensity = form.get("apply_all_intensity", "medium")
        if intensity not in ("light", "medium", "heavy"):
            intensity = "medium"
        all_items = (
            db.query(MenuItem)
            .filter(MenuItem.is_food.is_(True), MenuItem.is_active.is_(True))
            .all()
        )
        for item in all_items:
            db.add(IngredientDishMap(
                ingredient_id=ingredient_id,
                menu_item_id=item.id,
                intensity=intensity,
            ))
    else:
        # Re-insert individually checked dishes
        present_ids = [
            k.replace("dish_", "").replace("_present", "")
            for k in form.keys()
            if k.endswith("_present")
        ]
        for dish_id_str in present_ids:
            use = form.get(f"dish_{dish_id_str}_use")
            if not use:
                continue
            intensity = form.get(f"dish_{dish_id_str}_intensity", "medium")
            if intensity not in ("light", "medium", "heavy"):
                intensity = "medium"
            db.add(IngredientDishMap(
                ingredient_id=ingredient_id,
                menu_item_id=int(dish_id_str),
                intensity=intensity,
            ))
    db.commit()
    return RedirectResponse(f"/ingredients/mapping/{ingredient_id}?saved=1", status_code=303)


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
    return RedirectResponse(f"/ingredients/mapping/{ing.id}", status_code=303)
