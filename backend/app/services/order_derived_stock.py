"""Keep ingredient_stock_order_derived in sync with orders and purchases --
an independent, count-free stock model driven purely by a ledger (starting
baseline + purchases in - recipe usage out), being compared against the
physical-count model (ingredient_stock) via v_stock_model_comparison over a
two-month accuracy window.

Two event types besides the pre-existing 'baseline' snapshot:
- 'order_deduction': recipe usage from item_sales + order_items, reusing
  sales_stock.py's recipe-map/portion/unit-conversion helpers so both models
  consume an identical, already-correct grams/mL -> kg/l/pcs conversion
  table. A from-scratch reimplementation of that conversion is what produced
  a corrupted -427 l Oil reading (25 mL of oil per plate applied as 25 L).
- 'purchase_addition': restocks from the purchases table. Without this, any
  ingredient that got purchased after its baseline snapshot drifts
  increasingly (and misleadingly) negative, since the model only ever sees
  consumption and never sees the shelf being refilled.
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text

from app.core.clock import business_today
from app.services.sales_stock import _COMBO_SQL, _MAP_SQL, _MENU_SQL, _portion, _to_stock_unit

_BASELINE_DATE_SQL = text("""
    SELECT min(occurred_at) AS at FROM ingredient_stock_order_derived WHERE event_type = 'baseline'
""")

_LATEST_BALANCE_SQL = text("""
    SELECT ingredient_id, on_hand_qty, unit
      FROM ingredient_stock_order_derived d
     WHERE occurred_at = (
         SELECT max(occurred_at) FROM ingredient_stock_order_derived
          WHERE ingredient_id = d.ingredient_id
     )
""")

# Only events that happened *after* the baseline snapshot -- anything
# earlier is already reflected in the counted quantity the baseline
# recorded, and re-applying it would double-count. item_sales/purchases only
# carry a date (no time of day), so the baseline's own day is excluded
# entirely rather than guessing whether a given row fell before or after the
# snapshot moment within that day.
_UNPROCESSED_SALES_SQL = text("""
    SELECT id, item_name, qty, sale_date
      FROM item_sales
     WHERE sale_date < :today
       AND sale_date > :baseline_date
     ORDER BY sale_date, id
""")

_UNPROCESSED_ORDER_ITEMS_SQL = text("""
    SELECT oi.id, oi.menu_item_id, oi.quantity
      FROM order_items oi
      JOIN orders o ON o.id = oi.order_id
     WHERE o.status <> 'cancelled'
       AND oi.menu_item_id IS NOT NULL
       AND COALESCE(o.placed_at, o.created_at) >= :baseline_at
     ORDER BY oi.id
""")

_UNPROCESSED_PURCHASES_SQL = text("""
    SELECT id, ingredient_id, qty, unit::text AS unit, purchase_date
      FROM purchases
     WHERE deleted_at IS NULL
       AND purchase_date > :baseline_date
     ORDER BY purchase_date, id
""")

_EXISTING_EVENTS_SQL = text("""
    SELECT event_type, source_ref, ingredient_id
      FROM ingredient_stock_order_derived
     WHERE event_type IN ('order_deduction', 'purchase_addition')
""")

_INSERT_EVENT_SQL = text("""
    INSERT INTO ingredient_stock_order_derived
        (ingredient_id, event_type, source_ref, delta_qty, unit, on_hand_qty, occurred_at)
    VALUES
        (:ingredient_id, :event_type, :source_ref, :delta_qty, :unit, :on_hand_qty, now())
""")

# Purchases arrive already dimensioned in a physical unit (kg/g/l/ml/pcs) --
# a plain unit conversion, not the portion-is-grams-or-mL convention
# sales_stock.py's _to_stock_unit handles for recipe portions.
_PHYSICAL_UNIT_FACTOR = {
    ("kg", "g"): Decimal("1000"), ("g", "kg"): Decimal("0.001"),
    ("l", "ml"): Decimal("1000"), ("ml", "l"): Decimal("0.001"),
}


def _convert_physical_qty(qty: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    if from_unit == to_unit:
        return qty
    factor = _PHYSICAL_UNIT_FACTOR.get((from_unit, to_unit))
    return qty * factor if factor is not None else None


def _load_recipe_maps(db):
    maps = defaultdict(list)
    for row in db.execute(_MAP_SQL).mappings():
        maps[row["menu_item_id"]].append(dict(row))
    combos = defaultdict(list)
    for row in db.execute(_COMBO_SQL).mappings():
        combos[row["combo_menu_item_id"]].append(
            (row["component_menu_item_id"], Decimal(str(row["portion_factor"])))
        )

    def components(menu_id, factor=Decimal("1"), seen=frozenset()):
        if menu_id in seen:
            return []
        if not combos.get(menu_id):
            return [(menu_id, factor)]
        out = []
        for component_id, portion_factor in combos[menu_id]:
            out.extend(components(component_id, factor * portion_factor, seen | {menu_id}))
        return out

    return maps, components


def _usage_for_menu_item(maps, components, menu_id, qty) -> dict:
    """{ingredient_id: (qty_in_stock_unit, stock_unit)} for one sold quantity
    of one menu item, applying the same recipe-cost inputs sales_stock.py
    uses (recipe-role, non-Spices ingredients only)."""
    usage: dict = {}
    for dish_id, dish_factor in components(menu_id):
        for mapping in maps.get(dish_id, []):
            if mapping["cost_role"] != "recipe" or mapping["category"] == "Spices":
                continue
            portion = _portion(mapping)
            if portion is None:
                continue
            deduct = _to_stock_unit(
                Decimal(str(portion)) * Decimal(str(qty)) * dish_factor,
                mapping["ingredient_unit"], mapping["stock_unit"], mapping["pack_size_g"],
            )
            if deduct is None:
                continue
            prev_qty, _ = usage.get(mapping["ingredient_id"], (Decimal("0"), mapping["stock_unit"]))
            usage[mapping["ingredient_id"]] = (prev_qty + deduct, mapping["stock_unit"])
    return usage


def sync_order_derived_stock(db) -> dict:
    """Process every purchase / item_sales / order_items row not yet
    reflected in ingredient_stock_order_derived, keeping each ingredient's
    running order-derived balance current.

    Idempotent at (event_type, source_ref, ingredient_id) granularity -- not
    every ingredient has a seeded baseline yet, so a row can be partially
    applied now (the ingredients that do have a baseline) and finish
    catching up later once the rest are seeded, without double-applying the
    ones already recorded. Safe to call repeatedly (e.g. after every POS
    import, every order placed, every purchase logged).

    Known limitation: only *new* purchase rows add stock back. An edited or
    soft-deleted purchase does not yet reverse/adjust a previously-applied
    addition -- purchase edits/deletes are rare enough that this is an
    accepted gap for now rather than something silently pretended to be
    handled.
    """
    baseline_at = db.execute(_BASELINE_DATE_SQL).scalar()
    if baseline_at is None:
        return {
            "sales_seen": 0, "order_items_seen": 0, "purchases_seen": 0,
            "deductions_applied": 0, "additions_applied": 0,
            "skipped_no_baseline": 0, "skipped_unresolved": 0,
        }
    baseline_date = baseline_at.date()

    maps, components = _load_recipe_maps(db)
    menu_ids = {r["name"]: r["id"] for r in db.execute(_MENU_SQL).mappings()}

    balances = {
        row["ingredient_id"]: [Decimal(str(row["on_hand_qty"])), row["unit"]]
        for row in db.execute(_LATEST_BALANCE_SQL).mappings()
    }
    done = {
        (row["event_type"], row["source_ref"], row["ingredient_id"])
        for row in db.execute(_EXISTING_EVENTS_SQL).mappings()
    }

    deductions_applied = additions_applied = 0
    skipped_no_baseline = skipped_unresolved = 0

    def apply_event(event_type, source_ref, ingredient_id, signed_delta, unit):
        nonlocal deductions_applied, additions_applied, skipped_no_baseline
        if (event_type, source_ref, ingredient_id) in done:
            return
        balance = balances.get(ingredient_id)
        if balance is None or balance[1] != unit:
            # No baseline yet, or an incompatible historical unit -- same
            # rule sales_stock.py uses for ingredient_stock: an unknown or
            # mismatched starting point is left alone rather than guessed.
            skipped_no_baseline += 1
            return
        balance[0] += signed_delta
        db.execute(_INSERT_EVENT_SQL, {
            "ingredient_id": ingredient_id, "event_type": event_type, "source_ref": source_ref,
            "delta_qty": signed_delta, "unit": unit, "on_hand_qty": balance[0],
        })
        done.add((event_type, source_ref, ingredient_id))
        if event_type == "order_deduction":
            deductions_applied += 1
        else:
            additions_applied += 1

    def process_order(source_ref, menu_id, qty):
        nonlocal skipped_unresolved
        if menu_id is None:
            skipped_unresolved += 1
            return
        usage = _usage_for_menu_item(maps, components, menu_id, qty)
        if not usage:
            skipped_unresolved += 1
            return
        for ingredient_id, (qty_used, unit) in usage.items():
            apply_event("order_deduction", source_ref, ingredient_id, -qty_used, unit)

    sales = db.execute(_UNPROCESSED_SALES_SQL, {
        "today": business_today(), "baseline_date": baseline_date,
    }).mappings().all()
    for sale in sales:
        process_order(f"item_sales:{sale['id']}", menu_ids.get(sale["item_name"]), sale["qty"])

    order_items = db.execute(_UNPROCESSED_ORDER_ITEMS_SQL, {"baseline_at": baseline_at}).mappings().all()
    for item in order_items:
        process_order(f"order_item:{item['id']}", item["menu_item_id"], item["quantity"])

    purchases = db.execute(_UNPROCESSED_PURCHASES_SQL, {"baseline_date": baseline_date}).mappings().all()
    for p in purchases:
        balance = balances.get(p["ingredient_id"])
        if balance is None:
            skipped_no_baseline += 1
            continue
        added = _convert_physical_qty(Decimal(str(p["qty"])), p["unit"], balance[1])
        if added is None:
            skipped_unresolved += 1
            continue
        apply_event("purchase_addition", f"purchase:{p['id']}", p["ingredient_id"], added, balance[1])

    return {
        "sales_seen": len(sales), "order_items_seen": len(order_items), "purchases_seen": len(purchases),
        "deductions_applied": deductions_applied, "additions_applied": additions_applied,
        "skipped_no_baseline": skipped_no_baseline, "skipped_unresolved": skipped_unresolved,
    }
