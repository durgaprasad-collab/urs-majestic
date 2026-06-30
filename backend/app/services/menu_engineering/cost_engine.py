"""Derives per-dish food cost from purchase records + ingredient-dish intensity mapping.

Corrected cost-derivation model — fixes three root causes found in diagnosis:
  1. OVERHEAD SEPARATION
     Ingredients with cost_role='overhead' (e.g. cooking gas) are NOT charged
     per dish — a flat OVERHEAD_PER_DISH is added instead. cost_role='per_order'
     (e.g. packaging) is excluded entirely (it belongs on the order total, not
     each dish).
  2. REAL PORTIONS
     intensity (light/medium/heavy) maps to the ingredient's own
     portion_light_g / portion_medium_g / portion_heavy_g — real grams/ml —
     instead of a fraction of a whole purchase unit.
  3. UNIT-ENTRY GUARD
     Any derived per-gram/ml cost above IMPLAUSIBLE_PER_G is treated as a
     1000x unit-entry error (e.g. a litre bought but logged as ml), corrected
     and reported as an anomaly so the source purchase row can be fixed.

confidence levels:
  none     — dish has no ingredient-dish mapping at all
  building — mapping exists but a mapped recipe ingredient is missing a
             price (no menu purchases yet) or a portion size
  reliable — every mapped recipe ingredient has both a price and a portion
"""
import decimal
from dataclasses import dataclass, field
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.menu_item import MenuItem
from app.models.purchase import Purchase
from app.models.ingredient import Ingredient, IngredientDishMap

# Flat overhead added to every dish (gas + small misc), in rupees.
# Derive from data: (monthly gas+misc spend) / (dishes sold that month).
OVERHEAD_PER_DISH = decimal.Decimal("3.0")

# Any food ingredient costing more than this per gram/ml is almost certainly
# a unit-entry error (e.g. litres entered as ml). The most expensive real
# input here (paneer/cashew) is well under ₹1/g.
IMPLAUSIBLE_PER_G = decimal.Decimal("3.0")

_INTENSITY_COL = {
    "light": "portion_light_g",
    "medium": "portion_medium_g",
    "heavy": "portion_heavy_g",
}


@dataclass
class UnitAnomaly:
    ingredient_id: int
    name: str
    raw_cost_per_unit: float
    corrected_cost_per_g: float


def _ingredient_cost_per_g(db: Session) -> tuple[dict[int, decimal.Decimal], list[UnitAnomaly]]:
    """{ingredient_id: cost_per_gram_or_ml} for recipe ingredients with menu
    purchases, plus any unit-entry anomalies detected and auto-corrected.

    Normalization: cost in the ingredient's declared unit, converted to a
    per-gram (solids) / per-ml (liquids) basis.
        kg, l  -> divide by 1000
        g, ml  -> as-is
        pcs    -> skipped (not portionable by grams)
    """
    rows = (
        db.query(
            Ingredient.id,
            Ingredient.name,
            Ingredient.unit,
            (func.sum(Purchase.total_price) / func.nullif(func.sum(Purchase.qty), 0)).label("cost_per_unit"),
        )
        .join(Purchase, Purchase.ingredient_id == Ingredient.id)
        .filter(Ingredient.cost_role == "recipe", Purchase.usage_type == "menu")
        .group_by(Ingredient.id, Ingredient.name, Ingredient.unit)
        .all()
    )

    cost: dict[int, decimal.Decimal] = {}
    anomalies: list[UnitAnomaly] = []

    for ing_id, name, unit, cpu in rows:
        if cpu is None:
            continue
        cpu = decimal.Decimal(str(cpu))
        if unit in ("kg", "l"):
            per_g = cpu / 1000
        elif unit in ("g", "ml"):
            per_g = cpu
        else:  # pcs and anything else: not portionable by grams here
            continue

        # Unit-entry guard: implausible per-gram cost => 1000x error.
        if per_g > IMPLAUSIBLE_PER_G:
            corrected = per_g / 1000
            anomalies.append(UnitAnomaly(ing_id, name, float(cpu), float(corrected)))
            per_g = corrected

        cost[ing_id] = per_g

    return cost, anomalies


def _dish_recipe_costs(db: Session, ing_cost: dict[int, decimal.Decimal]) -> dict[int, tuple[decimal.Decimal, bool]]:
    """{menu_item_id: (recipe_cost_with_overhead, complete)} where `complete`
    is True only if every mapped recipe ingredient had both a price and a
    portion size (drives cost_confidence)."""
    maps = (
        db.query(IngredientDishMap, Ingredient)
        .join(Ingredient, Ingredient.id == IngredientDishMap.ingredient_id)
        .all()
    )

    acc: dict[int, list] = {}  # menu_item_id -> [cost, complete]
    for m, ing in maps:
        entry = acc.setdefault(m.menu_item_id, [decimal.Decimal("0"), True])
        if ing.cost_role != "recipe":
            continue  # overhead / per_order never priced per dish
        grams = getattr(ing, _INTENSITY_COL.get(m.intensity, "portion_medium_g"))
        per_g = ing_cost.get(ing.id)
        if grams is None or per_g is None:
            entry[1] = False  # incomplete data for this dish
            continue
        entry[0] += decimal.Decimal(str(grams)) * per_g

    return {mid: (v[0] + OVERHEAD_PER_DISH, v[1]) for mid, v in acc.items()}


def run_cost_engine(db: Session) -> dict:
    """Recompute derived_cost_per_unit, derived_food_cost_pct and
    cost_confidence for every active food menu item. Idempotent."""
    ing_cost, anomalies = _ingredient_cost_per_g(db)
    dish_cost = _dish_recipe_costs(db, ing_cost)

    items = (
        db.query(MenuItem)
        .filter(MenuItem.is_active.is_(True), MenuItem.is_food.is_(True))
        .all()
    )

    updated = 0
    reliable = 0
    building = 0
    none_count = 0

    for item in items:
        result = dish_cost.get(item.id)
        if result is None:
            item.derived_food_cost_pct = None
            item.derived_cost_per_unit = None
            item.cost_confidence = "none"
            none_count += 1
            continue

        cost, complete = result
        price = item.price
        if price and price > 0:
            item.derived_food_cost_pct = (cost / price).quantize(decimal.Decimal("0.0001"))
            item.derived_cost_per_unit = cost.quantize(decimal.Decimal("0.01"))
        else:
            item.derived_food_cost_pct = None
            item.derived_cost_per_unit = None
        item.cost_confidence = "reliable" if complete else "building"
        if complete:
            reliable += 1
        else:
            building += 1
        updated += 1

    db.commit()

    if anomalies:
        print("WARNING: unit-entry errors auto-corrected in cost engine (fix the source purchase rows):")
        for a in anomalies:
            print(f"  - {a.name}: Rs.{a.raw_cost_per_unit:.2f}/unit looked 1000x too high -> "
                  f"using Rs.{a.corrected_cost_per_g:.4f}/g. Likely litres/kg entered as ml/g.")

    return {
        "items_updated": updated,
        "reliable": reliable,
        "building": building,
        "none": none_count,
        "anomalies": [a.__dict__ for a in anomalies],
    }
