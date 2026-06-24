"""Cost engine trigger and reconciliation page."""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.purchase import Purchase
from app.models.ingredient import Ingredient, IngredientDishMap
from app.services.menu_engineering.cost_engine import run_cost_engine
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["engine"])


@router.post("/run-cost-engine")
async def run_engine(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    run_cost_engine(db)
    return RedirectResponse("/results?engine=1", status_code=303)


@router.get("/reconciliation", response_class=HTMLResponse)
def reconciliation(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    # Total cost by usage type
    agg = (
        db.query(Purchase.usage_type, func.sum(Purchase.total_price).label("total"))
        .group_by(Purchase.usage_type)
        .all()
    )
    totals = {row.usage_type: float(row.total) for row in agg}
    total_menu_cost = totals.get("menu", 0.0)
    total_personal_cost = totals.get("others_personal", 0.0)
    grand_total = total_menu_cost + total_personal_cost

    # Unmapped ingredients that have menu-usage purchases
    mapped_ingredient_ids = {
        row[0]
        for row in db.query(IngredientDishMap.ingredient_id).distinct().all()
    }
    menu_ingredient_ids = {
        row[0]
        for row in db.query(Purchase.ingredient_id).filter(Purchase.usage_type == "menu").distinct().all()
    }
    unmapped_ids = menu_ingredient_ids - mapped_ingredient_ids

    unmapped_cost = 0.0
    unmapped_names: list[str] = []
    if unmapped_ids:
        unmapped_agg = (
            db.query(Purchase.ingredient_id, func.sum(Purchase.total_price).label("total"))
            .filter(Purchase.ingredient_id.in_(unmapped_ids), Purchase.usage_type == "menu")
            .group_by(Purchase.ingredient_id)
            .all()
        )
        ing_map = {
            i.id: i.name
            for i in db.query(Ingredient).filter(Ingredient.id.in_(unmapped_ids)).all()
        }
        for row in unmapped_agg:
            unmapped_cost += float(row.total)
            unmapped_names.append(ing_map.get(row.ingredient_id, f"ID {row.ingredient_id}"))

    return _tmpl(request, "reconciliation.html", {
        "user": user,
        "total_menu_cost": total_menu_cost,
        "total_personal_cost": total_personal_cost,
        "grand_total": grand_total,
        "unmapped_cost": unmapped_cost,
        "unmapped_names": sorted(unmapped_names),
    })
