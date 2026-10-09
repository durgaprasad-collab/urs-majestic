"""Convert imported item sales into idempotent ingredient stock adjustments.

Walk-in sales come from the Petpooja item report (ledger prefix
petpooja_usage); delivery orders from the Zomato / Swiggy order uploads
(zomato_usage / swiggy_usage). Each channel keeps its own applied-usage
ledger, so re-importing any report is a no-op and a corrected one only
applies the difference.
"""

from collections import defaultdict, namedtuple
from datetime import date
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


# Every automatic stock row starts with one of these; anything else is a
# physical count a person entered.
AUTO_NOTE_PREFIXES = ("petpooja_usage:", "zomato_usage:", "swiggy_usage:", "purchase_auto:", "SYSTEM")
CHANNEL_LEDGER = {"petpooja": "petpooja_usage", "zomato": "zomato_usage", "swiggy": "swiggy_usage"}

Sale = namedtuple("Sale", "item_name qty sale_date")
# Delivery orders are deducted from stock from this date on. Older days are
# already reflected in counts/estimates; deducting them would double count.
DELIVERY_STOCK_FROM = date(2026, 9, 23)


def channel_sales(db, channel: str, dates) -> list:
    """Dish quantities per IST business date from a delivery channel's orders."""
    if not dates:
        return []
    rows = db.execute(text("""
        SELECT m.name AS item_name, (o.placed_at AT TIME ZONE :tz)::date AS sale_date, sum(oi.quantity) AS qty
          FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN menu_items m ON m.id = oi.menu_item_id
         WHERE o.channel = :channel AND (o.placed_at AT TIME ZONE :tz)::date = ANY(:dates)
         GROUP BY 1, 2
    """), {"channel": channel, "dates": list(dates), "tz": settings.BUSINESS_TIMEZONE}).mappings().all()
    return [Sale(r["item_name"], r["qty"], r["sale_date"]) for r in rows]


def adjust_stock_for_channel(db, channel: str, dates) -> dict:
    """Deduct a delivery channel's dish usage for the given dates. Caller commits."""
    dates = [d for d in dates if d >= DELIVERY_STOCK_FROM]
    return adjust_stock_for_sales(db, channel_sales(db, channel, dates), ledger=CHANNEL_LEDGER[channel], dates=dates)


def adjust_stock_for_sales(db, sales, ledger: str = "petpooja_usage", dates=None) -> dict:
    """Append only the usage difference for each date/ingredient.

    The latest `petpooja_usage:<date>:<qty>:<unit>` note is the applied-usage
    ledger. Therefore importing the same report twice is a no-op, while a
    corrected report subtracts or restores only its changed quantity.
    """
    calculated, unresolved = calculate_ingredient_usage(db, sales)
    # `dates` lets a channel re-upload clear a day whose orders all vanished.
    dates = sorted(set(dates or ()) | {sale.sale_date for sale in sales})
    if not dates:
        return {"adjusted": 0, "unchanged": 0, "initialized_zero": 0, "unresolved": unresolved}

    applied = {}
    ledger_rows = db.execute(text("""
        SELECT ingredient_id, note
          FROM ingredient_stock
         WHERE note LIKE :prefix
         ORDER BY counted_at, id
    """), {"prefix": f"{ledger}:%"}).mappings()
    for row in ledger_rows:
        # petpooja_usage:<date>:<qty>:<unit>[:<annotation>] -- a one-off repair
        # appended a 5th field to some notes; ignore it rather than the row.
        parts = row["note"].split(":")[:4]
        if len(parts) == 4:
            try:
                applied[(date_from_iso(parts[1]), row["ingredient_id"])] = (Decimal(parts[2]), parts[3])
            except Exception:
                continue

    adjusted = unchanged = initialized_zero = 0
    relevant_ledger_keys = {key for key in applied if key[0] in dates}
    for key in sorted(set(calculated) | relevant_ledger_keys):
        sale_date, ingredient_id = key
        old_used, old_unit = applied.get(key, (Decimal("0"), None))
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
                   -- Any row a person entered is a physical count: the stock
                   -- log (no note / reorder_required), the staff app
                   -- (app_count) and owner corrections (admin_edit). Only the
                   -- automatic rows are excluded.
                   AND COALESCE(note, '') NOT LIKE 'petpooja_usage:%'
                   AND COALESCE(note, '') NOT LIKE 'zomato_usage:%'
                   AND COALESCE(note, '') NOT LIKE 'swiggy_usage:%'
                   AND COALESCE(note, '') NOT LIKE 'purchase_auto:%'
                   AND COALESCE(note, '') NOT LIKE 'SYSTEM%'
                   -- The nightly count (~10 PM) is that day's closing count,
                   -- so it covers that day's sales; counts before 5 AM belong
                   -- to the previous evening (services/stock_count.count_day).
                   AND (timezone(:business_timezone, counted_at) - interval '5 hours')::date >= :sale_date
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
            "note": f"{ledger}:{sale_date.isoformat()}:{new_used.normalize()}:{unit}",
        })
        adjusted += 1
    return {"adjusted": adjusted, "unchanged": unchanged, "initialized_zero": initialized_zero, "unresolved": unresolved}


def date_from_iso(value: str):
    from datetime import date
    return date.fromisoformat(value)
