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
    SELECT w.d AS day, sum(w.qty * g.gr / base.b)::float AS portions
      FROM w JOIN g ON g.name = w.n CROSS JOIN base
     GROUP BY w.d ORDER BY w.d
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


def _portions(db, since: date) -> dict:
    rows = db.execute(_PORTIONS_SQL, {"since": since, "tz": settings.BUSINESS_TIMEZONE}).mappings().all()
    return {r["day"]: float(r["portions"] or 0) for r in rows}


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
    portions = _portions(db, since)
    asof = db.execute(text("SELECT max(sale_date) FROM item_sales")).scalar()
    g_per, n_cal = _calibrate(batches, readings, portions)

    cur = next((b for b in batches if b["yield_kg"] is not None and b["finished_date"] is None), None)
    out = {"batches": batches, "readings": readings, "g_per": g_per, "n_cal": n_cal, "asof": asof, "today": today,
           "current": None, "daily": [], "lead": LEAD_DAYS}
    if cur is None:
        return out

    start = cur["started_use_date"] or cur["batch_date"]
    yield_kg = float(cur["yield_kg"])
    rs = readings.get(cur["id"], [])
    last = asof or today
    left, daily, used_total = yield_kg, [], 0.0
    day = start
    while day <= last:
        p = portions.get(day, 0.0)
        kg = p * g_per / 1000
        used_total += kg
        left -= kg
        reading = next((r for r in rs if r["reading_date"] == day), None)
        if reading:  # a weighed reading beats the estimate
            left = float(reading["kg_left"])
        daily.append({"day": day, "portions": p, "kg": kg, "left": left, "reading": reading is not None,
                      "has_sales": day in portions})
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
    out["daily"] = list(reversed(daily))
    return out
