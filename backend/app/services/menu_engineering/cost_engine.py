"""Derives per-dish food cost from purchase records + ingredient-dish intensity mapping.

Intensity weights: light=1, medium=2, heavy=3.
Cost is allocated from the most-recent 90-day window of *menu* purchases.
confidence levels:
  none     — no purchase data or no mappings at all
  building — 1-89 days of data or fewer than 5 menu purchases
  reliable — 90+ days with 5+ menu purchases
"""
import decimal
from datetime import date, timedelta
from sqlalchemy.orm import Session
from app.models.menu_item import MenuItem
from app.models.purchase import Purchase
from app.models.ingredient import Ingredient, IngredientDishMap

_INTENSITY_WEIGHT = {"light": 1, "medium": 2, "heavy": 3}
_RELIABLE_DAYS = 90
_RELIABLE_MIN_PURCHASES = 5


def run_cost_engine(db: Session) -> dict:
    """Compute derived_food_cost_pct and derived_cost_per_unit for all active menu items."""

    menu_purchases = (
        db.query(Purchase)
        .filter(Purchase.usage_type == "menu")
        .order_by(Purchase.purchase_date)
        .all()
    )

    if not menu_purchases:
        # No data — reset all items to confidence=none
        db.query(MenuItem).filter(MenuItem.is_active.is_(True)).update(
            {"derived_food_cost_pct": None, "derived_cost_per_unit": None, "cost_confidence": "none"},
            synchronize_session="fetch",
        )
        db.commit()
        return {"confidence": "none", "total_menu_cost": 0, "items_updated": 0}

    # Determine data window
    oldest = min(p.purchase_date for p in menu_purchases)
    newest = max(p.purchase_date for p in menu_purchases)
    days_span = (newest - oldest).days + 1
    purchase_count = len(menu_purchases)

    if days_span >= _RELIABLE_DAYS and purchase_count >= _RELIABLE_MIN_PURCHASES:
        confidence = "reliable"
    else:
        confidence = "building"

    # Restrict to most-recent 90-day window for cost allocation
    window_start = newest - timedelta(days=_RELIABLE_DAYS - 1)
    window_purchases = [p for p in menu_purchases if p.purchase_date >= window_start]

    # Total cost per ingredient in window
    ingredient_costs: dict[int, decimal.Decimal] = {}
    for p in window_purchases:
        ingredient_costs.setdefault(p.ingredient_id, decimal.Decimal("0"))
        ingredient_costs[p.ingredient_id] += p.total_price

    # Load all ingredient-dish mappings
    all_maps = db.query(IngredientDishMap).all()
    if not all_maps:
        db.query(MenuItem).filter(MenuItem.is_active.is_(True)).update(
            {"derived_food_cost_pct": None, "derived_cost_per_unit": None, "cost_confidence": "none"},
            synchronize_session="fetch",
        )
        db.commit()
        return {"confidence": "none", "total_menu_cost": float(sum(ingredient_costs.values())), "items_updated": 0}

    # Sum total intensity-weighted shares per ingredient
    # ingredient → {dish_id: weight}
    from collections import defaultdict
    ingredient_dish_weights: dict[int, dict[int, int]] = defaultdict(dict)
    dish_ingredient_weights: dict[int, dict[int, int]] = defaultdict(dict)

    for m in all_maps:
        w = _INTENSITY_WEIGHT.get(m.intensity, 2)
        ingredient_dish_weights[m.ingredient_id][m.menu_item_id] = w
        dish_ingredient_weights[m.menu_item_id][m.ingredient_id] = w

    # Allocate ingredient cost to each dish
    dish_allocated_cost: dict[int, decimal.Decimal] = defaultdict(lambda: decimal.Decimal("0"))

    for ing_id, cost in ingredient_costs.items():
        dish_weights = ingredient_dish_weights.get(ing_id, {})
        if not dish_weights:
            continue
        total_weight = sum(dish_weights.values())
        for dish_id, w in dish_weights.items():
            dish_allocated_cost[dish_id] += cost * decimal.Decimal(w) / decimal.Decimal(total_weight)

    # Write back to menu_items
    items = db.query(MenuItem).filter(MenuItem.is_active.is_(True)).all()
    updated = 0
    for item in items:
        allocated = dish_allocated_cost.get(item.id)
        if allocated is None or allocated == 0:
            # No mapping or no purchases for its ingredients
            item.derived_food_cost_pct = None
            item.derived_cost_per_unit = None
            item.cost_confidence = "none"
        else:
            price = float(item.price)
            if price > 0:
                derived_pct = float(allocated) / price
                item.derived_food_cost_pct = decimal.Decimal(str(round(derived_pct, 4)))
                item.derived_cost_per_unit = decimal.Decimal(str(round(float(allocated), 2)))
            else:
                item.derived_food_cost_pct = None
                item.derived_cost_per_unit = None
            item.cost_confidence = confidence
            updated += 1

    db.commit()

    return {
        "confidence": confidence,
        "days_span": days_span,
        "purchase_count": purchase_count,
        "total_menu_cost": float(sum(ingredient_costs.values())),
        "items_updated": updated,
    }
