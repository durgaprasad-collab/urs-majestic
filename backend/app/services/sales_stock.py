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
    SELECT m.menu_item_id, m.ingredient_id, m.intensity, m.grams_override, m.portion_override_g,
           i.unit::text AS ingredient_unit, i.pack_size_g, i.category, i.cost_role::text,
           i.portion_light_g, i.portion_medium_g, i.portion_heavy_g,
           COALESCE(v.unit::text, i.unit::text) AS stock_unit
      FROM ingredient_dish_map m
      JOIN ingredients i ON i.id = m.ingredient_id
      LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
     WHERE i.is_active
       AND NOT m.stock_from_batch  -- gravy batch basis (2026-10-20): these leave stock when the batch is logged, not per plate
""")

_COMBO_SQL = text("""
    SELECT combo_menu_item_id, component_menu_item_id, portion_factor
      FROM combo_components
     WHERE component_menu_item_id IS NOT NULL
""")

_MENU_SQL = text("SELECT id, name FROM menu_items")


def _portion(row) -> Decimal | None:
    # A weighed portion (grams_override) wins, same as v_dish_recipe_cost
    # uses for food cost, so stock and costing agree on what a dish uses.
    if row["grams_override"] is not None:
        return Decimal(str(row["grams_override"]))
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
    elif ingredient_unit == "pcs" and pack_size_g and stock_unit in ("kg", "g"):
        # Bought by the packet/bunch but stocked by weight (packet purchases
        # are converted to kg on entry): the portion grams go straight across.
        base_qty, base_unit = amount, "g"
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
AUTO_NOTE_PREFIXES = ("petpooja_usage:", "zomato_usage:", "swiggy_usage:", "pace_usage:", "purchase_auto:", "transfer_in:", "SYSTEM")
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
        # The latest physical count on or after the sales day decides how much
        # of that day's usage still has to come off:
        #   counted on a later business day -> it already includes the whole
        #     day, nothing comes off (the ledger still advances);
        #   counted during the sales day itself (a mid-day correction, or the
        #     ~10 PM nightly count while service runs till 1 AM) -> only the
        #     share of the day's sales made after the count comes off;
        #   no count since -> the whole day comes off the latest balance.
        # Any row a person entered is a physical count (stock log, staff app,
        # owner edits); automatic rows are excluded. Business days roll over at
        # 5 AM in the business timezone (services/stock_count.count_day).
        count = db.execute(text("""
            SELECT counted_at,
                   (timezone(:business_timezone, counted_at) - interval '5 hours')::date AS count_day
              FROM ingredient_stock
             WHERE ingredient_id = :ingredient_id
               AND COALESCE(note, '') NOT LIKE 'petpooja_usage:%'
               AND COALESCE(note, '') NOT LIKE 'zomato_usage:%'
               AND COALESCE(note, '') NOT LIKE 'swiggy_usage:%'
               AND COALESCE(note, '') NOT LIKE 'pace_usage:%'
               AND COALESCE(note, '') NOT LIKE 'purchase_auto:%'
               AND COALESCE(note, '') NOT LIKE 'transfer_in:%'
               AND COALESCE(note, '') NOT LIKE 'SYSTEM%'
               AND (timezone(:business_timezone, counted_at) - interval '5 hours')::date >= :sale_date
             ORDER BY counted_at DESC LIMIT 1
        """), {
            "ingredient_id": ingredient_id,
            "sale_date": sale_date,
            "business_timezone": settings.BUSINESS_TIMEZONE,
        }).mappings().first()
        if count is None:
            share = Decimal("1")
        elif count["count_day"] > sale_date:
            share = Decimal("0")
        else:
            share = share_after(db, ledger, sale_date, count["counted_at"])
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
        else:
            # No zero floor (2026-10-25): clipping at 0 silently lost usage, and the next
            # purchase then restored a stock level that never existed. A negative balance
            # is a visible shortfall: a missing purchase bill or an overstated portion.
            balance = Decimal(str(latest["on_hand_qty"])) - delta * share
        db.execute(text("""
            INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (:ingredient_id, :balance, CAST(:unit AS unit_type), NULL, :note)
        """), {
            "ingredient_id": ingredient_id, "balance": balance, "unit": unit,
            "note": f"{ledger}:{sale_date.isoformat()}:{new_used.normalize()}:{unit}",
        })
        adjusted += 1
    return {"adjusted": adjusted, "unchanged": unchanged, "initialized_zero": initialized_zero, "unresolved": unresolved}


def share_after(db, ledger: str, sale_date, at) -> Decimal:
    """Share (0-1) of a sales day's takings made after `at`, by amount.

    Petpooja uses the uploaded order listing's times (petpooja_order_times);
    Zomato/Swiggy use their own order times. If that day's times aren't in
    yet, the usual pattern of past days (by time of day) stands in."""
    if ledger == "petpooja_usage":
        day_sql = """SELECT sum(amount) AS total, sum(amount) FILTER (WHERE created_at > :at) AS after
                       FROM petpooja_order_times WHERE business_date = :d"""
        hist_sql = """SELECT created_at AS t, amount AS a FROM petpooja_order_times
                       WHERE business_date > :d - 60 AND business_date <> :d"""
        params = {}
    else:
        channel = ledger.split("_")[0]
        day_sql = """SELECT sum(total_amount) AS total, sum(total_amount) FILTER (WHERE placed_at > :at) AS after
                       FROM orders WHERE channel = :ch
                        AND (timezone(:tz, placed_at) - interval '6 hours')::date = :d"""
        hist_sql = """SELECT placed_at AS t, total_amount AS a FROM orders WHERE channel = :ch
                       AND placed_at > now() - interval '60 days'"""
        params = {"ch": channel}
    params.update({"d": sale_date, "at": at, "tz": settings.BUSINESS_TIMEZONE})
    row = db.execute(text(day_sql), params).mappings().first()
    if row and row["total"]:
        return Decimal(str(row["after"] or 0)) / Decimal(str(row["total"]))
    # Usual pattern: minutes past the 6 AM day start, so after-midnight orders sort last.
    from app.core.clock import business_tz
    def mins(t):
        lt = t.astimezone(business_tz())
        return ((lt.hour - 6) % 24) * 60 + lt.minute
    cut = mins(at)
    hist = db.execute(text(hist_sql), params).all()
    total = sum(Decimal(str(a or 0)) for _, a in hist)
    if not total:
        return Decimal("1")  # nothing to go on: take the whole day off, as before
    return sum(Decimal(str(a or 0)) for t, a in hist if mins(t) > cut) / total


def date_from_iso(value: str):
    from datetime import date
    return date.fromisoformat(value)


# Items sales can't take off stock (spices, packaging, milk, cleaning...) come
# off at their buying pace instead, one day at a time with each Petpooja day,
# so their estimate falls between counts rather than only ever rising with
# purchases. Ledger: pace_usage:<date>:<qty>:<unit>, one row per item per day.
PACE_FROM = date(2026, 10, 17)
PACE_SKIP_CATEGORIES = {"Apparel", "Equipment", "Overhead", "Staff"}


def adjust_stock_for_pace(db, dates) -> dict:
    """Deduct one day's buying-pace use per item for each sales day. Items that
    sales deducted in the last 30 days are skipped (they're covered). A count
    made after the day already includes it; one made during the day takes
    only the share after it. Idempotent per item and day. Caller commits."""
    from app.services.buy_inbox import _resale_daily

    dates = sorted({d for d in dates if d >= PACE_FROM})
    if not dates:
        return {"adjusted": 0}
    by_sales = {r[0] for r in db.execute(text("""
        SELECT DISTINCT ingredient_id FROM ingredient_stock
         WHERE note ~ '^(petpooja|zomato|swiggy)_usage:' AND counted_at > now() - interval '30 days'
    """))}
    resale = _resale_daily(db)
    items = db.execute(text("""
        SELECT v.ingredient_id, v.daily_consumption, v.unit::text AS unit, i.category, i.name
          FROM v_ingredient_reorder_forecast v JOIN ingredients i ON i.id = v.ingredient_id
         WHERE i.is_active AND v.daily_consumption > 0
    """)).mappings().all()
    done = {(r[0], r[1]) for r in db.execute(text("""
        SELECT ingredient_id, split_part(note, ':', 2) FROM ingredient_stock WHERE note LIKE 'pace_usage:%'
    """))}
    adjusted = 0
    for it in items:
        iid = it["ingredient_id"]
        if iid in by_sales or (it["category"] or "") in PACE_SKIP_CATEGORIES or it["name"] == "Cooking Gas":
            continue
        rate = Decimal(str(resale.get(iid) or it["daily_consumption"]))
        for d in dates:
            if (iid, d.isoformat()) in done:
                continue
            latest = db.execute(text("""
                SELECT on_hand_qty, unit::text AS unit FROM ingredient_stock
                 WHERE ingredient_id = :i ORDER BY counted_at DESC, id DESC LIMIT 1 FOR UPDATE
            """), {"i": iid}).mappings().first()
            if latest is None or latest["unit"] != it["unit"]:
                continue  # no baseline in this unit: nothing to deduct from
            count = db.execute(text("""
                SELECT counted_at, (timezone(:tz, counted_at) - interval '5 hours')::date AS count_day
                  FROM ingredient_stock
                 WHERE ingredient_id = :i AND counted_by IS NOT NULL
                   AND NOT (COALESCE(note, '') ~ '^(petpooja_usage|zomato_usage|swiggy_usage|pace_usage|purchase_auto|transfer_in|SYSTEM)')
                   AND (timezone(:tz, counted_at) - interval '5 hours')::date >= :d
                 ORDER BY counted_at DESC LIMIT 1
            """), {"i": iid, "d": d, "tz": settings.BUSINESS_TIMEZONE}).mappings().first()
            if count is None:
                share = Decimal("1")
            elif count["count_day"] > d:
                share = Decimal("0")
            else:
                share = share_after(db, "petpooja_usage", d, count["counted_at"])
            balance = Decimal(str(latest["on_hand_qty"])) - rate * share  # no zero floor, see above
            db.execute(text("""
                INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
                VALUES (:i, :b, CAST(:u AS unit_type), NULL, :n)
            """), {"i": iid, "b": balance, "u": it["unit"], "n": f"pace_usage:{d.isoformat()}:{format(rate.normalize(), 'f')}:{it['unit']}"})
            adjusted += 1
    return {"adjusted": adjusted}
