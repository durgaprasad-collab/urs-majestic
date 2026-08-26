"""Deduct recipe-mapped ingredient usage into ingredient_stock_order_derived --
an independent, count-free stock model driven purely by orders (Petpooja
item_sales + this app's own order_items), being compared against the
physical-count model (ingredient_stock) via v_stock_model_comparison over a
two-month accuracy window.

Reuses sales_stock.py's recipe-map/portion/unit-conversion helpers so both
models consume an identical, already-correct grams/mL -> kg/l/pcs conversion
table. A from-scratch reimplementation of that conversion is what produced
the corrupted -427 l Oil reading (25 mL of oil per plate applied as 25 L)
this module replaces.
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text

from app.core.clock import business_today
from app.services.sales_stock import _COMBO_SQL, _MAP_SQL, _MENU_SQL, _portion, _to_stock_unit

_BASELINE_DATE_SQL = text("""
    SELECT min(occurred_at) AS at FROM ingredient_stock_order_derived WHERE event_type = 'baseline'
""")

# Only sales/orders that happened *after* the baseline snapshot -- anything
# earlier is already reflected in the counted quantity the baseline
# recorded, and re-deducting it would double-count that consumption.
# item_sales only carries a date (no time of day), so the baseline's own day
# is excluded entirely rather than guessing whether a given sale fell before
# or after the snapshot moment within that day.
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

_EXISTING_DEDUCTIONS_SQL = text("""
    SELECT source_ref, ingredient_id
      FROM ingredient_stock_order_derived
     WHERE event_type = 'order_deduction'
""")

_LATEST_BALANCE_SQL = text("""
    SELECT ingredient_id, on_hand_qty, unit
      FROM ingredient_stock_order_derived d
     WHERE occurred_at = (
         SELECT max(occurred_at) FROM ingredient_stock_order_derived
          WHERE ingredient_id = d.ingredient_id
     )
""")

_INSERT_DEDUCTION_SQL = text("""
    INSERT INTO ingredient_stock_order_derived
        (ingredient_id, event_type, source_ref, delta_qty, unit, on_hand_qty, occurred_at)
    VALUES
        (:ingredient_id, 'order_deduction', :source_ref, :delta_qty, :unit, :on_hand_qty, now())
""")


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


def apply_order_derived_deductions(db) -> dict:
    """Process every item_sales / order_items row not yet reflected in
    ingredient_stock_order_derived, deducting recipe usage from each
    ingredient's running order-derived balance.

    Idempotent at (source_ref, ingredient_id) granularity -- not every
    ingredient has a seeded baseline yet, so a sale can be partially applied
    now (the ingredients that do have a baseline) and finish catching up
    later once the rest are seeded, without double-deducting the ones
    already recorded. Safe to call repeatedly (e.g. after every POS import
    and every order placed).
    """
    baseline_at = db.execute(_BASELINE_DATE_SQL).scalar()
    if baseline_at is None:
        return {
            "sales_seen": 0, "order_items_seen": 0, "deductions_applied": 0,
            "skipped_no_baseline": 0, "skipped_unresolved": 0,
        }

    maps, components = _load_recipe_maps(db)
    menu_ids = {r["name"]: r["id"] for r in db.execute(_MENU_SQL).mappings()}

    balances = {
        row["ingredient_id"]: [Decimal(str(row["on_hand_qty"])), row["unit"]]
        for row in db.execute(_LATEST_BALANCE_SQL).mappings()
    }
    done = {(row["source_ref"], row["ingredient_id"]) for row in db.execute(_EXISTING_DEDUCTIONS_SQL).mappings()}

    applied = skipped_no_baseline = skipped_unresolved = 0

    def process(source_ref, menu_id, qty):
        nonlocal applied, skipped_no_baseline, skipped_unresolved
        if menu_id is None:
            skipped_unresolved += 1
            return
        usage = _usage_for_menu_item(maps, components, menu_id, qty)
        if not usage:
            skipped_unresolved += 1
            return
        for ingredient_id, (qty_used, unit) in usage.items():
            if (source_ref, ingredient_id) in done:
                continue
            balance = balances.get(ingredient_id)
            if balance is None or balance[1] != unit:
                # No baseline yet, or an incompatible historical unit -- same
                # rule sales_stock.py uses for ingredient_stock: an unknown or
                # mismatched starting point is left alone rather than guessed.
                skipped_no_baseline += 1
                continue
            balance[0] -= qty_used
            db.execute(_INSERT_DEDUCTION_SQL, {
                "ingredient_id": ingredient_id, "source_ref": source_ref,
                "delta_qty": -qty_used, "unit": unit, "on_hand_qty": balance[0],
            })
            done.add((source_ref, ingredient_id))
            applied += 1

    sales = db.execute(_UNPROCESSED_SALES_SQL, {
        "today": business_today(), "baseline_date": baseline_at.date(),
    }).mappings().all()
    for sale in sales:
        process(f"item_sales:{sale['id']}", menu_ids.get(sale["item_name"]), sale["qty"])

    order_items = db.execute(_UNPROCESSED_ORDER_ITEMS_SQL, {"baseline_at": baseline_at}).mappings().all()
    for item in order_items:
        process(f"order_item:{item['id']}", item["menu_item_id"], item["quantity"])

    return {
        "sales_seen": len(sales), "order_items_seen": len(order_items),
        "deductions_applied": applied, "skipped_no_baseline": skipped_no_baseline,
        "skipped_unresolved": skipped_unresolved,
    }
