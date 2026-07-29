"""Derives per-dish food cost from purchase records + ingredient-dish intensity mapping.

Corrected cost-derivation model — fixes three root causes found in diagnosis:
  1. OVERHEAD SEPARATION
     Ingredients with cost_role='overhead' (e.g. cooking gas) are NOT charged
     per dish — a flat OVERHEAD_PER_DISH is added instead, because gas is a fixed
     cost that doesn't scale with the exact number of dishes.
     cost_role='per_order' (e.g. packaging) IS a variable per-order cost, so its
     total menu spend is amortized across dishes sold (PER_ORDER_PER_DISH) and
     added to every dish. Gas stays flat; packaging follows real volume.
     Ingredients in category='Spices' are handled the same amortized way
     (SPICE_PER_DISH): used in nearly every dish but too small/variable to
     portion-map, so their spend is spread over dishes and they're skipped from
     per-portion costing (no double count).
     All of this overhead is applied ONCE PER SOLD ITEM (in run_cost_engine,
     after combos are assembled) — a combo is one sold line, so it carries the
     overhead once, not once per component.
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

COMBOS
  Menu items with rows in combo_components are costed in a second pass, from
  their components' already-derived per-unit costs, instead of from their own
  ingredient_dish_map rows (those are legacy/duplicate for combo items — only
  cost_role='per_order' rows, e.g. packaging, are left alone since they were
  never part of per-dish cost anyway):
      derived_cost = SUM(portion_factor * component's derived_cost_per_unit)
                    + SUM(fixed_cost where component_menu_item_id IS NULL)
  A combo's cost_confidence is 'building' (never 'reliable') if any of its
  component rows is a guess, or if a referenced component is itself missing
  cost data or not 'reliable'.
"""
import decimal
from dataclasses import dataclass, field
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.menu_item import MenuItem
from app.models.purchase import Purchase
from app.models.ingredient import Ingredient, IngredientDishMap
from app.models.item_sale import ItemSale
from app.models.combo import ComboComponent

# Flat overhead added to every dish for FIXED costs (gas + small misc), in rupees.
# Gas is deliberately flat, not amortized per dish — the kitchen burns roughly the
# same gas regardless of the exact dish count, so it's a fixed cost, not variable.
OVERHEAD_PER_DISH = decimal.Decimal("3.0")

# Any food ingredient costing more than this per gram/ml is almost certainly
# a unit-entry error (e.g. litres entered as ml). The most expensive real
# input here (paneer/cashew) is well under ₹1/g.
IMPLAUSIBLE_PER_G = decimal.Decimal("3.0")

# Ingredients in this category are amortized across dishes (see _spice_per_dish)
# rather than portion-mapped per recipe. Set an ingredient's category to this to
# add it to the spice pool.
_SPICE_CATEGORY = "Spices"

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

    Each purchase row is normalized to a gram (solids) / ml (liquids) base using
    THAT ROW's OWN unit -- never the ingredient's declared unit -- then summed:
    per_g = sum(total_price) / sum(qty_in_base). This makes a row logged in a
    different unit than the ingredient (500 g on a kg item, a kg/L row on an ml
    item, etc.) impossible to miscount -- the recurring 1000x-cost bug that hit
    spring onion, butter, cream, lemon and milk.
        kg, l  -> qty * 1000
        g, ml  -> qty
        pcs    -> qty * pack_size_g (grams per piece) when set, so items bought by
                  the bunch/piece but portioned by grams (coriander, mint) still
                  get a per-gram cost; skipped if no pack_size_g.
    """
    rows = (
        db.query(
            Ingredient.id,
            Ingredient.name,
            Ingredient.category,
            Ingredient.pack_size_g,
            Purchase.qty,
            Purchase.unit,
            Purchase.total_price,
        )
        .join(Purchase, Purchase.ingredient_id == Ingredient.id)
        .filter(Ingredient.cost_role == "recipe", Purchase.usage_type == "menu",
                Purchase.deleted_at.is_(None))
        .all()
    )

    # ingredient_id -> [spend, base_qty (g/ml), name, category, pack_size_g]
    acc: dict[int, list] = {}
    for ing_id, name, category, pack_size_g, qty, unit, price in rows:
        if qty is None or price is None:
            continue
        qty = decimal.Decimal(str(qty))
        unit = getattr(unit, "value", unit)  # ORM enum -> plain string
        if unit in ("kg", "l"):
            base = qty * 1000
        elif unit in ("g", "ml"):
            base = qty
        elif unit == "pcs" and pack_size_g:
            base = qty * decimal.Decimal(str(pack_size_g))
        else:  # pcs with no pack weight, or an unknown unit -- not portionable by grams
            continue
        e = acc.setdefault(ing_id, [decimal.Decimal("0"), decimal.Decimal("0"), name, category, pack_size_g])
        e[0] += decimal.Decimal(str(price))
        e[1] += base

    cost: dict[int, decimal.Decimal] = {}
    anomalies: list[UnitAnomaly] = []

    for ing_id, (spend, base, name, category, pack_size_g) in acc.items():
        if base <= 0:
            continue
        per_g = spend / base
        # Implausibility guard catches a genuine 1000x data error. SKIP it for
        # spices: they are amortized by total spend (not portion-costed), and some
        # (cardamom, saffron) are legitimately > IMPLAUSIBLE_PER_G, so applying it
        # would false-flag them and wrongly divide the cost down.
        if category != _SPICE_CATEGORY and per_g > IMPLAUSIBLE_PER_G:
            corrected = per_g / 1000
            anomalies.append(UnitAnomaly(ing_id, name, float(per_g), float(corrected)))
            per_g = corrected
        cost[ing_id] = per_g

    return cost, anomalies


def _per_order_per_dish(db: Session) -> decimal.Decimal:
    """Variable per-order overhead (packaging) amortized across dishes sold.

    Packaging (cost_role='per_order') is a real cost of every order but isn't
    portioned into a recipe. Spread its total menu spend over the number of
    dishes sold so each dish carries its average share:
        sum(per_order menu spend) / sum(units sold)
    Returns 0 when there are no sales yet (so a fresh DB never divides by zero).
    Note: this averages across ALL dishes (dine-in included); it's an estimate,
    not a per-delivery-order figure.
    """
    spend = (
        db.query(func.coalesce(func.sum(Purchase.total_price), 0))
        .join(Ingredient, Ingredient.id == Purchase.ingredient_id)
        .filter(Ingredient.cost_role == "per_order", Purchase.usage_type == "menu",
                Purchase.deleted_at.is_(None))
        .scalar()
    )
    units = db.query(func.coalesce(func.sum(ItemSale.qty), 0)).scalar()
    spend = decimal.Decimal(str(spend or 0))
    units = decimal.Decimal(str(units or 0))
    return (spend / units) if units > 0 else decimal.Decimal("0")


def _spice_per_dish(db: Session) -> decimal.Decimal:
    """Spice cost amortized across dishes sold — same shape as packaging.

    Individual spices (category='Spices') are used in almost every dish in
    amounts too small and variable to portion-map to each recipe, so their total
    menu spend is spread over dishes sold and added to each dish:
        sum(Spices-category menu spend) / sum(units sold)
    Spices are therefore NOT portioned per recipe — _dish_recipe_costs skips
    category='Spices' so a spice mapped to a dish can never double-count.
    """
    spend = (
        db.query(func.coalesce(func.sum(Purchase.total_price), 0))
        .join(Ingredient, Ingredient.id == Purchase.ingredient_id)
        .filter(Ingredient.category == _SPICE_CATEGORY, Purchase.usage_type == "menu",
                Purchase.deleted_at.is_(None))
        .scalar()
    )
    units = db.query(func.coalesce(func.sum(ItemSale.qty), 0)).scalar()
    spend = decimal.Decimal(str(spend or 0))
    units = decimal.Decimal(str(units or 0))
    return (spend / units) if units > 0 else decimal.Decimal("0")


def _dish_recipe_costs(
    db: Session,
    ing_cost: dict[int, decimal.Decimal],
    skip_menu_item_ids: frozenset[int] = frozenset(),
) -> dict[int, tuple[decimal.Decimal, bool]]:
    """{menu_item_id: (recipe_cost, complete)} — portioned recipe-ingredient cost
    ONLY, WITHOUT the per-dish overhead. `complete` is True only if every mapped
    recipe ingredient had both a price and a portion size (drives cost_confidence).

    Overhead (flat gas + amortized packaging + amortized spices) is deliberately
    NOT added here — the caller adds it once per SOLD ITEM, so a combo built from
    N components is not charged overhead N times (see run_cost_engine).

    `skip_menu_item_ids` (combo items) are left out entirely — their own
    ingredient_dish_map rows are legacy/duplicate; they're costed separately
    from combo_components in a second pass, see _combo_costs.
    """
    maps = (
        db.query(IngredientDishMap, Ingredient)
        .join(Ingredient, Ingredient.id == IngredientDishMap.ingredient_id)
        .all()
    )

    acc: dict[int, list] = {}  # menu_item_id -> [cost, complete]
    for m, ing in maps:
        if m.menu_item_id in skip_menu_item_ids:
            continue
        entry = acc.setdefault(m.menu_item_id, [decimal.Decimal("0"), True])
        if ing.cost_role != "recipe":
            continue  # overhead (flat) / per_order (amortized) not priced per portion
        if ing.category == _SPICE_CATEGORY:
            continue  # spices are amortized per dish, not portioned — no double count
        grams = getattr(ing, _INTENSITY_COL.get(m.intensity, "portion_medium_g"))
        per_g = ing_cost.get(ing.id)
        if grams is None or per_g is None:
            entry[1] = False  # incomplete data for this dish
            continue
        entry[0] += decimal.Decimal(str(grams)) * per_g

    return {mid: (v[0], v[1]) for mid, v in acc.items()}


def _combo_costs(
    db: Session, component_cost: dict[int, tuple[decimal.Decimal, bool]]
) -> dict[int, tuple[decimal.Decimal, bool]]:
    """{combo_menu_item_id: (derived_cost, complete)} built from
    combo_components, using already-derived component dish costs
    (`component_cost`, the non-combo pass of _dish_recipe_costs).

    complete is False ("building") if any component row is a guess, or if a
    referenced component has no cost data / isn't itself 'reliable'.
    """
    rows = db.query(ComboComponent).all()

    acc: dict[int, list] = {}  # combo_menu_item_id -> [cost, complete]
    for c in rows:
        entry = acc.setdefault(c.combo_menu_item_id, [decimal.Decimal("0"), True])
        if c.is_guess:
            entry[1] = False

        if c.component_menu_item_id is not None:
            component = component_cost.get(c.component_menu_item_id)
            if component is None:
                entry[1] = False
                continue
            comp_cost, comp_complete = component
            if not comp_complete:
                entry[1] = False
            entry[0] += c.portion_factor * comp_cost

        if c.fixed_cost is not None:
            entry[0] += c.fixed_cost

    return {mid: (v[0], v[1]) for mid, v in acc.items()}


def run_cost_engine(db: Session) -> dict:
    """Recompute derived_cost_per_unit, derived_food_cost_pct and
    cost_confidence for every active food menu item. Idempotent.

    This is the SINGLE source of truth for menu_items cost. It is invoked both by
    the "Run cost engine" button and by the purchase-edit resync path
    (app/web/audit.py:resync_derived_costs), so a purchase change and its cost
    recompute always agree. Mutates the session but does NOT commit -- the caller
    commits."""
    ing_cost, anomalies = _ingredient_cost_per_g(db)
    # Per-sold-item overhead, added ONCE below — flat gas + amortized packaging +
    # amortized spices. Applied after combos are assembled so a combo (one sold
    # line) carries it once, not once per component.
    fixed_per_dish = OVERHEAD_PER_DISH + _per_order_per_dish(db) + _spice_per_dish(db)

    combo_ids = frozenset(
        row[0] for row in db.query(ComboComponent.combo_menu_item_id).distinct().all()
    )

    # Pass 1: recipe-only cost for component (non-combo) dishes.
    # Pass 2: combos, from those recipe-only component costs.
    recipe = _dish_recipe_costs(db, ing_cost, skip_menu_item_ids=combo_ids)
    recipe.update(_combo_costs(db, recipe))
    # Overhead once per sold item — standalone dish OR combo.
    dish_cost = {mid: (cost + fixed_per_dish, complete)
                 for mid, (cost, complete) in recipe.items()}

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

    # No commit here -- the caller owns the transaction. Both callers (the "Run
    # cost engine" button and the purchase-edit resync path) need this to run
    # INSIDE their transaction so a purchase change and its cost recompute commit
    # atomically. Callers must db.commit() afterward.

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
