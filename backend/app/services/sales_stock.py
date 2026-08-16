"""Convert imported item sales into idempotent ingredient stock adjustments."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text

from app.core.config import settings


_MAP_SQL = text("""
    SELECT m.menu_item_id, m.ingredient_id, m.intensity, m.portion_override_g,
           i.unit::text AS ingredient_unit, i.pack_size_g, i.category, i.cost_role::text,
           i.portion_light_g, i.portion_medium_g, i.portion_heavy_g,
           COALESCE(v.unit::text, i.unit::text) AS stock_unit
      FROM ingredient_dish_map m
      JOIN ingredients i ON i.id = m.ingredient_id
      LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
     WHERE i.is_active
""")

_COMBO_SQL = text("""
    SELECT combo_menu_item_id, component_menu_item_id, portion_factor
      FROM combo_components
     WHERE component_menu_item_id IS NOT NULL
""")

_MENU_SQL = text("SELECT id, name FROM menu_items")


def _portion(row) -> Decimal | None:
    if row["portion_override_g"] is not None:
        return Decimal(str(row["portion_override_g"]))
    return row.get(f"portion_{row['intensity']}_g")


def _to_stock_unit(amount: Decimal, ingredient_unit: str, stock_unit: str, pack_size_g) -> Decimal | None:
    # Portion inputs are grams for solids, ml for liquids. Piece-based produce
    # can still be calculated when its measured grams-per-piece is configured.
    if ingredient_unit == "kg":
        base_qty, base_unit = amount / Decimal("1000"), "kg"
    elif ingredient_unit == "g":
        base_qty, base_unit = amount, "g"
    elif ingredient_unit == "l":
        base_qty, base_unit = amount / Decimal("1000"), "l"
    elif ingredient_unit == "ml":
        base_qty, base_unit = amount, "ml"
    elif ingredient_unit == "pcs":
        # A configured pack weight means the portion is grams; otherwise the
        # existing portion input is already a piece count (e.g. one bottle).
        base_qty = amount / Decimal(str(pack_size_g)) if pack_size_g else amount
        base_unit = "pcs"
    else:
        return None

    conversions = {
        ("kg", "g"): Decimal("1000"), ("g", "kg"): Decimal("0.001"),
        ("l", "ml"): Decimal("1000"), ("ml", "l"): Decimal("0.001"),
        # The existing cost engine combines gram and ml purchase bases for
        # sauces/purees using the standard kitchen approximation 1 ml ~= 1 g.
        ("ml", "g"): Decimal("1"), ("g", "ml"): Decimal("1"),
        ("ml", "kg"): Decimal("0.001"), ("kg", "ml"): Decimal("1000"),
        ("l", "g"): Decimal("1000"), ("g", "l"): Decimal("0.001"),
    }
    if base_unit == stock_unit:
        return base_qty
    factor = conversions.get((base_unit, stock_unit))
    return base_qty * factor if factor is not None else None


def calculate_ingredient_usage(db, sales) -> tuple[dict, set[str]]:
    """Return {(sale_date, ingredient_id): (qty, unit)} from recipe inputs."""
    menu_ids = {r["name"]: r["id"] for r in db.execute(_MENU_SQL).mappings()}
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

    usage = defaultdict(lambda: [Decimal("0"), None])
    unresolved: set[str] = set()
    for sale in sales:
        menu_id = menu_ids.get(sale.item_name)
        if menu_id is None:
            unresolved.add(sale.item_name)
            continue
        for dish_id, dish_factor in components(menu_id):
            for mapping in maps.get(dish_id, []):
                # Match the existing recipe-cost inputs: gas/packaging and the
                # globally amortised spice bucket are not per-dish deductions.
                if mapping["cost_role"] != "recipe" or mapping["category"] == "Spices":
                    continue
                portion = _portion(mapping)
                if portion is None:
                    unresolved.add(f"{sale.item_name} -> ingredient #{mapping['ingredient_id']}")
                    continue
                qty = _to_stock_unit(
                    Decimal(str(portion)) * Decimal(str(sale.qty)) * dish_factor,
                    mapping["ingredient_unit"], mapping["stock_unit"], mapping["pack_size_g"],
                )
                if qty is None:
                    unresolved.add(f"{sale.item_name} -> ingredient #{mapping['ingredient_id']} unit")
                    continue
                key = (sale.sale_date, mapping["ingredient_id"])
                usage[key][0] += qty
                usage[key][1] = mapping["stock_unit"]
    return {key: (value[0], value[1]) for key, value in usage.items()}, unresolved


def adjust_stock_for_sales(db, sales) -> dict:
    """Append only the usage difference for each date/ingredient.

    The latest `petpooja_usage:<date>:<qty>:<unit>` note is the applied-usage
    ledger. Therefore importing the same report twice is a no-op, while a
    corrected report subtracts or restores only its changed quantity.
    """
    calculated, unresolved = calculate_ingredient_usage(db, sales)
    dates = sorted({sale.sale_date for sale in sales})
    if not dates:
        return {"adjusted": 0, "unchanged": 0, "initialized_zero": 0, "unresolved": unresolved}

    ledger = {}
    ledger_rows = db.execute(text("""
        SELECT ingredient_id, note
          FROM ingredient_stock
         WHERE note LIKE 'petpooja_usage:%'
         ORDER BY counted_at, id
    """)).mappings()
    for row in ledger_rows:
        parts = row["note"].split(":")
        if len(parts) == 4:
            try:
                ledger[(date_from_iso(parts[1]), row["ingredient_id"])] = (Decimal(parts[2]), parts[3])
            except Exception:
                continue

    adjusted = unchanged = initialized_zero = 0
    relevant_ledger_keys = {key for key in ledger if key[0] in dates}
    for key in sorted(set(calculated) | relevant_ledger_keys):
        sale_date, ingredient_id = key
        old_used, old_unit = ledger.get(key, (Decimal("0"), None))
        new_used, unit = calculated.get(key, (Decimal("0"), old_unit))
        delta = new_used - old_used
        if abs(delta) < Decimal("0.000001"):
            unchanged += 1
            continue
        latest = db.execute(text("""
            SELECT on_hand_qty, unit::text AS unit
              FROM ingredient_stock
             WHERE ingredient_id = :ingredient_id
             ORDER BY counted_at DESC, id DESC
             LIMIT 1 FOR UPDATE
        """), {"ingredient_id": ingredient_id}).mappings().first()
        # A physical count entered on a later business date is authoritative:
        # it already includes all sales from ``sale_date``.  Re-importing that
        # day's Petpooja report must still advance the idempotency ledger, but
        # must not deduct the same usage again from the observed balance.
        #
        # Use the business timezone rather than the database/server date.  A
        # count just after midnight IST is the normal closing count for the
        # preceding trading day even though Render/Postgres may still be on the
        # previous UTC date.
        covered_by_physical_count = db.execute(text("""
            SELECT EXISTS (
                SELECT 1
                  FROM ingredient_stock
                 WHERE ingredient_id = :ingredient_id
                   AND (note IS NULL OR note = 'reorder_required')
                   AND timezone(:business_timezone, counted_at)::date > :sale_date
            )
        """), {
            "ingredient_id": ingredient_id,
            "sale_date": sale_date,
            "business_timezone": settings.BUSINESS_TIMEZONE,
        }).scalar_one()
        if latest is None:
            # No physical baseline exists. Record a conservative zero balance
            # plus the usage ledger so the same report cannot deduct twice.
            balance = Decimal("0")
            initialized_zero += 1
        elif latest["unit"] != unit:
            # An incompatible historical balance is safer left untouched than
            # silently treating litres, kilograms, or pieces as equivalent.
            initialized_zero += 1
            balance = Decimal("0")
        elif covered_by_physical_count:
            balance = Decimal(str(latest["on_hand_qty"]))
        else:
            balance = max(Decimal("0"), Decimal(str(latest["on_hand_qty"])) - delta)
        db.execute(text("""
            INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (:ingredient_id, :balance, CAST(:unit AS unit_type), NULL, :note)
        """), {
            "ingredient_id": ingredient_id, "balance": balance, "unit": unit,
            "note": f"petpooja_usage:{sale_date.isoformat()}:{new_used.normalize()}:{unit}",
        })
        adjusted += 1
    return {"adjusted": adjusted, "unchanged": unchanged, "initialized_zero": initialized_zero, "unresolved": unresolved}


def date_from_iso(value: str):
    from datetime import date
    return date.fromisoformat(value)
