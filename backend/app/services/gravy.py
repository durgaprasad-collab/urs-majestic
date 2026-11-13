"""Gravy tracker: when the last batch was made, how much is left, when to make the next.

Dishes that use the cashew gravy base are the ones mapped to the "Cashew gravy"
ingredient (stock_from_batch). Each counts as a portion weighted by its mapped
grams (full gravy dish 14 g -> 1.0, biryani/pulav 7 g -> 0.5). Daily use is
portions x grams-per-portion. Grams-per-portion is learned from past batches
that have a "kg left" reading; until one exists, DEFAULT_G is used. A reading on
the current batch re-anchors the estimate.
"""
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from sqlalchemy import text

from app.core.clock import business_today
from app.core.config import settings

DEFAULT_G = 220.0   # g of gravy per weighted portion (26-Oct batch: 18.85 kg over ~86 portions)
LEAD_DAYS = 1       # make the next batch this many days before the last one runs out
TREND_DAYS = 14     # recent trading days used for the daily rate

# What goes into a batch, per kg of gravy yield, from the owner-weighed 3-Nov batch
# (22.88 kg yield). Quantities are in each ingredient's own stock unit.
_REF_YIELD = 22.88
_REF_ISSUED = [  # (ingredient name, qty issued for the 3-Nov batch)
    ("Onion", 26.12), ("Tomato", 1.981), ("Cashew gravy", 0.706), ("Oil", 2.0), ("Butter", 0.25),
    ("Coriander powder", 0.03255), ("Chilli powder", 0.0651), ("Chicken Masala (veg spice blend)", 0.0651),
    ("Turmeric Powder", 13.02), ("Kashmiri chilli powder", 32.55), ("Jeera Whole (cumin seed)", 32.55),
    ("Salt", 0.03255), ("Kasuri Methi", 32.55), ("Bay leaf", 23.33), ("Star anise", 23.33),
    ("Cinnamon/Cassia (Dalchini)", 23.34),
]


_UNIT_X = {("g", "kg"): 0.001, ("kg", "g"): 1000.0, ("ml", "l"): 0.001, ("l", "ml"): 1000.0}


def recipe_for(yield_kg: float) -> list[tuple[str, float]]:
    return [(n, q * yield_kg / _REF_YIELD) for n, q in _REF_ISSUED]


def issue_batch(db, batch_date: date, yield_kg: float) -> int:
    """Take the batch's ingredients off stock (one ledger row each, scaled to the yield).
    Skips an ingredient that already has a gravy_batch row for that date, so a batch
    entered by hand earlier is not deducted twice. Returns how many were deducted.
    The note starts with SYSTEM: the table only allows a negative balance for system
    rows, and a batch that overdraws stock should show as a shortfall, not clip at 0."""
    tag = f"gravy_batch_{batch_date.isoformat()}"
    done = 0
    for name, qty in recipe_for(yield_kg):
        ing = db.execute(text("SELECT id, unit::text AS unit FROM ingredients WHERE name = :n"), {"n": name}).mappings().first()
        if ing is None:
            continue
        if db.execute(text("SELECT 1 FROM ingredient_stock WHERE ingredient_id = :i AND note LIKE :p LIMIT 1"),
                      {"i": ing["id"], "p": "%" + tag + "%"}).first():
            continue
        last = db.execute(text("""SELECT on_hand_qty, unit::text AS unit FROM ingredient_stock
                                  WHERE ingredient_id = :i ORDER BY counted_at DESC, id DESC LIMIT 1 FOR UPDATE"""),
                          {"i": ing["id"]}).mappings().first()
        unit, q = ing["unit"], qty
        if last is not None and last["unit"] != unit:  # the ledger can be in kg while the item is in g
            f = _UNIT_X.get((unit, last["unit"]))
            if f is None:
                continue  # incompatible unit: leave it for a manual count rather than guess
            unit, q = last["unit"], qty * f
        bal = (float(last["on_hand_qty"]) if last else 0.0) - q
        db.execute(text("""INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
                           VALUES (:i, :b, CAST(:u AS unit_type), NULL, :n)"""),
                   {"i": ing["id"], "b": round(bal, 4), "u": unit,
                    "n": f"SYSTEM {tag}: issued {q:.4g} {unit} to gravy batch (auto, scaled to {yield_kg:.2f} kg yield)"})
        done += 1
    return done


_PORTIONS_SQL = text("""
    WITH g AS (
        SELECT mi.name, COALESCE(m.grams_override, m.portion_override_g) AS gr
          FROM ingredient_dish_map m
          JOIN ingredients i ON i.id = m.ingredient_id
          JOIN menu_items mi ON mi.id = m.menu_item_id
         WHERE i.name = 'Cashew gravy' AND m.stock_from_batch
    ), base AS (SELECT max(gr) AS b FROM g),
    w AS (
        SELECT sale_date AS d, item_name AS n, qty FROM item_sales WHERE sale_date >= :since
        UNION ALL
        SELECT (o.placed_at AT TIME ZONE :tz)::date, mi.name, oi.quantity
          FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN menu_items mi ON mi.id = oi.menu_item_id
         WHERE (o.placed_at AT TIME ZONE :tz)::date >= :since
    )
    SELECT w.d AS day, w.n AS dish, sum(w.qty)::float AS qty, (g.gr / base.b)::float AS weight
      FROM w JOIN g ON g.name = w.n CROSS JOIN base
     GROUP BY w.d, w.n, g.gr, base.b ORDER BY w.d
""")


def ensure_tables(db) -> None:
    """Create gravy_readings on first use (same DDL as migration 0059, so either order works)."""
    if db.execute(text("SELECT to_regclass('public.gravy_readings')")).scalar() is not None:
        return
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS gravy_readings (
            id serial PRIMARY KEY,
            batch_id integer NOT NULL REFERENCES gravy_batches(id) ON DELETE CASCADE,
            reading_date date NOT NULL,
            kg_left numeric(6,2) NOT NULL CHECK (kg_left >= 0),
            note text, created_by integer, created_at timestamptz NOT NULL DEFAULT now())"""))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_gravy_readings_batch ON gravy_readings (batch_id, reading_date)"))
    db.execute(text("""
        INSERT INTO gravy_readings (batch_id, reading_date, kg_left, note)
        SELECT id, DATE '2026-11-03', 3.0, 'Owner: 3 kg left on 3-Nov (seeded from batch notes)'
          FROM gravy_batches WHERE batch_date = DATE '2026-10-26'"""))
    db.commit()


def _portions(db, since: date) -> tuple[dict, dict]:
    """({day: weighted portions}, {day: {dish: qty}})"""
    rows = db.execute(_PORTIONS_SQL, {"since": since, "tz": settings.BUSINESS_TIMEZONE}).mappings().all()
    portions, dishes = defaultdict(float), defaultdict(dict)
    for r in rows:
        portions[r["day"]] += r["qty"] * r["weight"]
        dishes[r["day"]][r["dish"]] = r["qty"]
    return dict(portions), dict(dishes)


def _calibrate(batches, readings, portions) -> tuple[float, int]:
    """Median grams per portion over past batches that have a reading."""
    samples = []
    for b in batches:
        rs = readings.get(b["id"])
        if not rs or b["yield_kg"] is None:
            continue
        r = rs[-1]
        start = b["started_use_date"] or b["batch_date"]
        p = sum(v for d, v in portions.items() if start <= d <= r["reading_date"])
        used = float(b["yield_kg"]) - float(r["kg_left"])
        if p >= 20 and used > 1:
            samples.append(used * 1000 / p)
    return (median(samples), len(samples)) if samples else (DEFAULT_G, 0)


def page_data(db) -> dict:
    ensure_tables(db)
    batches = [dict(r) for r in db.execute(text("""
        SELECT b.id, b.batch_date, b.yield_kg, b.gross_kg, b.vessel_kg, b.started_use_date, b.finished_date,
               b.batch_cost_inr, b.notes, v.days_lasted, v.kg_per_day, v.cost_per_kg
          FROM gravy_batches b JOIN v_gravy_batches v ON v.id = b.id
         ORDER BY b.batch_date DESC, b.id DESC""")).mappings()]
    readings = defaultdict(list)
    for r in db.execute(text("SELECT id, batch_id, reading_date, kg_left, note FROM gravy_readings ORDER BY reading_date, id")).mappings():
        readings[r["batch_id"]].append(dict(r))

    today = business_today()
    since = min([b["batch_date"] for b in batches] + [today - timedelta(days=60)])
    portions, dishes = _portions(db, since)
    asof = db.execute(text("SELECT max(sale_date) FROM item_sales")).scalar()
    g_per, n_cal = _calibrate(batches, readings, portions)

    cur = next((b for b in batches if b["yield_kg"] is not None and b["finished_date"] is None), None)
    out = {"batches": batches, "readings": readings, "g_per": g_per, "n_cal": n_cal, "asof": asof, "today": today,
           "current": None, "daily": [], "lead": LEAD_DAYS,
           "recipe": [{"name": n, "per_kg": q / _REF_YIELD} for n, q in _REF_ISSUED]}
    if cur is None:
        return out

    start = cur["started_use_date"] or cur["batch_date"]
    yield_kg = float(cur["yield_kg"])
    rs = readings.get(cur["id"], [])
    last = asof or today
    left, daily, used_total, by_dish = yield_kg, [], 0.0, {}
    day = start
    while day <= last:
        p = portions.get(day, 0.0)
        kg = p * g_per / 1000
        used_total += kg
        left -= kg
        reading = next((r for r in rs if r["reading_date"] == day), None)
        if reading:  # a weighed reading beats the estimate
            left = float(reading["kg_left"])
        dd = dishes.get(day, {})
        daily.append({"day": day, "portions": p, "kg": kg, "left": left, "reading": reading is not None,
                      "has_sales": day in portions, "dishes": sum(dd.values()),
                      "breakup": sorted(dd.items(), key=lambda x: -x[1])})
        for n, q in dd.items():
            by_dish[n] = by_dish.get(n, 0.0) + q
        day += timedelta(days=1)

    recent = [portions[d] for d in sorted(portions) if d <= last][-TREND_DAYS:]
    per_day_kg = (sum(recent) / len(recent)) * g_per / 1000 if recent else None
    days_left = (max(left, 0) / per_day_kg) if per_day_kg else None
    runout = (last + timedelta(days=int(days_left))) if days_left is not None else None
    make_by = (runout - timedelta(days=LEAD_DAYS)) if runout else None
    level = "" if make_by is None else ("bad" if make_by <= today else ("warn" if make_by <= today + timedelta(days=1) else "good"))
    out["current"] = {
        "batch": cur, "start": start, "days_in_use": (today - start).days + 1, "yield_kg": yield_kg,
        "left": max(left, 0.0), "used": yield_kg - max(left, 0.0), "per_day_kg": per_day_kg,
        "days_left": days_left, "runout": runout, "make_by": make_by, "level": level,
        "last_reading": rs[-1] if rs else None,
        "no_sales_days": [d["day"] for d in daily if not d["has_sales"] and d["day"] < last],
    }
    total_dishes = sum(by_dish.values())
    out["total_dishes"] = total_dishes
    out["by_dish"] = [{"dish": n, "qty": q, "pct": q / total_dishes * 100 if total_dishes else 0}
                      for n, q in sorted(by_dish.items(), key=lambda x: -x[1])]
    out["daily"] = list(reversed(daily))
    return out
