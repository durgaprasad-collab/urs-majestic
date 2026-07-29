"""Cost engine trigger and reconciliation page."""
from collections import defaultdict
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.purchase import Purchase
from app.models.ingredient import Ingredient, IngredientDishMap
from app.models.menu_item import MenuItem
from app.models.item_sale import ItemSale
from app.services.menu_engineering.cost_engine import run_cost_engine
from app.web.deps import _tmpl, require_user

_INTENSITY_PORTION_COL = {
    "light": "portion_light_g",
    "medium": "portion_medium_g",
    "heavy": "portion_heavy_g",
}

router = APIRouter(tags=["engine"])


@router.post("/run-cost-engine")
async def run_engine(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    run_cost_engine(db)
    db.commit()
    return RedirectResponse("/results?engine=1", status_code=303)


@router.get("/reconciliation", response_class=HTMLResponse)
def reconciliation(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    # Optional ingredient filter. Scopes the cost cards, the purchase summary and
    # the usage-by-dish table to a single ingredient. Unmapped-ingredient warnings
    # are deliberately NOT scoped — that panel is a global data-hygiene alert and
    # hiding it behind a filter would let unmapped cost go unnoticed.
    raw_ing = request.query_params.get("ingredient_id", "")
    filter_id = int(raw_ing) if raw_ing.isdigit() else None
    filter_ing = db.get(Ingredient, filter_id) if filter_id else None
    if filter_ing is None:
        filter_id = None

    # All ingredients that have at least one purchase — the only ones that can
    # produce rows below, so the dropdown never offers a choice that yields nothing.
    purchased_ids = {
        r[0]
        for r in db.query(Purchase.ingredient_id)
        .filter(Purchase.deleted_at.is_(None))
        .distinct()
        .all()
    }
    filter_options = (
        db.query(Ingredient)
        .filter(Ingredient.id.in_(purchased_ids))
        .order_by(Ingredient.name)
        .all()
        if purchased_ids else []
    )

    # Total cost by usage type
    agg_q = db.query(Purchase.usage_type, func.sum(Purchase.total_price).label("total")).filter(
        Purchase.deleted_at.is_(None)
    )
    if filter_id:
        agg_q = agg_q.filter(Purchase.ingredient_id == filter_id)
    agg = agg_q.group_by(Purchase.usage_type).all()
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
        for row in db.query(Purchase.ingredient_id)
        .filter(Purchase.usage_type == "menu", Purchase.deleted_at.is_(None))
        .distinct()
        .all()
    }
    unmapped_ids = menu_ingredient_ids - mapped_ingredient_ids

    unmapped_cost = 0.0
    unmapped_names: list[str] = []
    if unmapped_ids:
        unmapped_agg = (
            db.query(Purchase.ingredient_id, func.sum(Purchase.total_price).label("total"))
            .filter(
                Purchase.ingredient_id.in_(unmapped_ids),
                Purchase.usage_type == "menu",
                Purchase.deleted_at.is_(None),
            )
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

    # Per-ingredient purchase summary (restocking interval + avg qty)
    purch_q = (
        db.query(Purchase)
        .filter(Purchase.deleted_at.is_(None))
        .order_by(Purchase.ingredient_id, Purchase.purchase_date)
    )
    if filter_id:
        purch_q = purch_q.filter(Purchase.ingredient_id == filter_id)
    all_purchases = purch_q.all()
    ing_groups: dict[int, list] = defaultdict(list)
    for p in all_purchases:
        ing_groups[p.ingredient_id].append(p)

    ings_by_id = {
        i.id: i
        for i in db.query(Ingredient).filter(Ingredient.id.in_(set(ing_groups.keys()))).all()
    } if ing_groups else {}

    ingredient_summary: list[dict] = []
    for ing_id, purchases in ing_groups.items():
        ing = ings_by_id.get(ing_id)
        if not ing:
            continue
        dates = sorted(p.purchase_date for p in purchases)
        total_qty = sum(float(p.qty) for p in purchases)
        avg_qty = total_qty / len(purchases)
        total_spent = sum(float(p.total_price) for p in purchases)
        if len(dates) >= 2:
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_days: float | None = sum(gaps) / len(gaps)
        else:
            avg_days = None
        ingredient_summary.append({
            "name": ing.name,
            "count": len(purchases),
            "total_qty": total_qty,
            "avg_qty": avg_qty,
            "unit": purchases[-1].unit,
            "avg_days": avg_days,
            "total_spent": total_spent,
            "last_date": max(dates),
        })
    ingredient_summary.sort(key=lambda x: x["name"])

    # Ingredient usage % per dish — share of each ingredient's actual
    # consumption (portion size x units sold) going to each mapped dish.
    sales_agg = (
        db.query(ItemSale.item_name, func.sum(ItemSale.qty).label("units"))
        .group_by(ItemSale.item_name)
        .all()
    )
    units_sold_by_name = {row.item_name: float(row.units) for row in sales_agg}

    maps_q = (
        db.query(IngredientDishMap, Ingredient, MenuItem)
        .join(Ingredient, Ingredient.id == IngredientDishMap.ingredient_id)
        .join(MenuItem, MenuItem.id == IngredientDishMap.menu_item_id)
        .filter(Ingredient.cost_role == "recipe")
    )
    if filter_id:
        maps_q = maps_q.filter(IngredientDishMap.ingredient_id == filter_id)
    maps = maps_q.all()

    usage_groups: dict[str, list[dict]] = defaultdict(list)
    for m, ing, item in maps:
        portion = getattr(ing, _INTENSITY_PORTION_COL.get(m.intensity, "portion_medium_g"))
        units_sold = units_sold_by_name.get(item.name, 0.0)
        grams_used = float(portion) * units_sold if portion is not None else None
        usage_groups[ing.name].append({
            "dish": item.name,
            "intensity": m.intensity,
            "units_sold": units_sold,
            "grams_used": grams_used,
        })

    ingredient_usage: list[dict] = []
    for ing_name, rows in usage_groups.items():
        total_grams = sum(r["grams_used"] for r in rows if r["grams_used"] is not None)
        for r in rows:
            if r["grams_used"] is not None and total_grams > 0:
                r["pct"] = r["grams_used"] / total_grams * 100
            else:
                r["pct"] = None
        rows.sort(key=lambda r: (r["pct"] is None, -(r["pct"] or 0)))
        ingredient_usage.append({"name": ing_name, "rows": rows})
    ingredient_usage.sort(key=lambda x: x["name"])

    # When a filter is on but the ingredient has no recipe mapping, the usage
    # table would silently vanish. Say why instead.
    usage_empty_reason = None
    if filter_ing and not ingredient_usage:
        if filter_ing.cost_role != "recipe":
            usage_empty_reason = (
                f"{filter_ing.name} is not a recipe-role ingredient "
                f"(cost_role = {filter_ing.cost_role}), so it has no per-dish usage split."
            )
        else:
            usage_empty_reason = (
                f"{filter_ing.name} is not mapped to any dish yet."
            )

    return _tmpl(request, "reconciliation.html", {
        "user": user,
        "total_menu_cost": total_menu_cost,
        "total_personal_cost": total_personal_cost,
        "grand_total": grand_total,
        "unmapped_cost": unmapped_cost,
        "unmapped_names": sorted(unmapped_names),
        "ingredient_summary": ingredient_summary,
        "ingredient_usage": ingredient_usage,
        "filter_options": filter_options,
        "filter_id": filter_id,
        "filter_ing": filter_ing,
        "usage_empty_reason": usage_empty_reason,
    })
