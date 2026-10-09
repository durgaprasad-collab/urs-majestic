"""Gas: two cylinders (Tandoor, Kitchen) weighed by staff every night at
12:30 AM in the staff app.

Staff type the scale reading (cylinder included); gas on hand is
gross - TARE_KG. Use between two readings of the same cylinder is the drop
in weight; when a reading is marked is_new_cylinder (a swap), use is what
was left in the old one plus what's already gone from the new one, assuming
it started full (FULL_KG). A reading belongs to the business night it closes:
12:30 AM on the 10th is the night of the 9th (same 05:00 rollover as stock
counts, services/stock_count.count_day).
"""
import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_now, business_tz
from app.services.stock_count import count_day

CYLINDERS = (("tandoor", "Tandoor"), ("kitchen", "Kitchen"))
LABEL = dict(CYLINDERS)
TARE_KG = Decimal("20")      # empty cylinder, owner-confirmed 2026-10-09
FULL_KG = Decimal("19.2")    # gas in a full commercial cylinder
REMINDER_HOUR, REMINDER_MINUTE = 0, 30
AVG_NIGHTS = 7
CHART_NIGHTS = 14
LOW_DAYS = Decimal("1.5")    # warn the owner when a cylinder has less than this left
# A gap longer than this between two readings isn't a night's use (the log
# lapsed); the later reading just restarts the chain.
STALE_HOURS = 72

_READINGS = text("""
    SELECT g.id, g.cylinder, g.recorded_at, g.gross_kg, g.tare_kg, g.is_new_cylinder, g.note, u.name AS by_name
      FROM gas_readings g LEFT JOIN users u ON u.id = g.recorded_by
     WHERE g.cylinder_role = 'in_use'
     ORDER BY g.recorded_at, g.id
""")


def night_of(at: datetime.datetime) -> datetime.date:
    return count_day(at)


def _readings(db: Session) -> dict[str, list[dict]]:
    tz = business_tz()
    out = {k: [] for k, _ in CYLINDERS}
    for r in db.execute(_READINGS).mappings():
        d = dict(r)
        d["recorded_at"] = d["recorded_at"].astimezone(tz)
        d["net"] = Decimal(str(d["gross_kg"])) - Decimal(str(d["tare_kg"]))
        d["night"] = night_of(d["recorded_at"])
        d["used"] = None
        out.setdefault(d["cylinder"], []).append(d)
    for rows in out.values():
        chain_usage(rows)
    return out


def chain_usage(rows: list[dict]) -> list[dict]:
    """Fill rows[i]["used"] / ["hours"] for one cylinder's chronological
    readings (each with recorded_at, net, is_new_cylinder)."""
    for prev, cur in zip(rows, rows[1:]):
        cur["hours"] = (cur["recorded_at"] - prev["recorded_at"]).total_seconds() / 3600
        if cur["hours"] > STALE_HOURS:
            continue
        if cur["is_new_cylinder"]:
            cur["used"] = max(prev["net"], Decimal(0)) + max(FULL_KG - cur["net"], Decimal(0))
        else:
            cur["used"] = max(prev["net"] - cur["net"], Decimal(0))
    return rows


def _rate(rows: list[dict], now: datetime.datetime) -> Decimal | None:
    """kg/day over the readings of the last AVG_NIGHTS days."""
    since = now - datetime.timedelta(days=AVG_NIGHTS)
    recent = [r for r in rows if r["used"] is not None and r["recorded_at"] >= since and r.get("hours")]
    hours = sum(r["hours"] for r in recent)
    if hours < 12:
        return None
    return sum((r["used"] for r in recent), Decimal(0)) / Decimal(str(hours / 24))


def cylinder_state(db: Session) -> dict:
    now = business_now()
    tonight = count_day(now)
    readings = _readings(db)
    cyls = []
    for key, label in CYLINDERS:
        rows = readings.get(key, [])
        last = rows[-1] if rows else None
        rate = _rate(rows, now)
        swaps = [r for r in rows if r["is_new_cylinder"]]
        lasted = None
        if len(swaps) >= 2:
            between = [r for r in rows if swaps[-2]["recorded_at"] < r["recorded_at"] <= swaps[-1]["recorded_at"]]
            if all((r.get("hours") or 0) <= STALE_HOURS for r in between):  # no lapse in the log
                lasted = (swaps[-1]["recorded_at"] - swaps[-2]["recorded_at"]).total_seconds() / 86400
        stale = last is not None and (now - last["recorded_at"]).total_seconds() / 3600 > STALE_HOURS
        left = max(last["net"], Decimal(0)) if last and not stale else None
        cyls.append({
            "stale": stale,
            "key": key, "label": label, "last": last, "left": left, "rate": rate,
            "pct": float(min(left / FULL_KG, 1) * 100) if left is not None else 0,
            "days_left": (left / rate) if (left is not None and rate) else None,
            "fitted": swaps[-1]["recorded_at"] if swaps else None,
            "lasted": lasted,
            "logged_tonight": last if last and last["night"] == tonight else None,
            "rows": rows,
        })
    return {"tonight": tonight, "cylinders": cyls, "readings": readings}


def nightly(readings: dict[str, list[dict]], nights: int) -> list[dict]:
    """Per-night use per cylinder for the last `nights` nights, newest first."""
    by_night: dict[datetime.date, dict] = {}
    for key, rows in readings.items():
        for r in rows:
            n = by_night.setdefault(r["night"], {"night": r["night"], "rows": {}, "used": {}})
            n["rows"][key] = r
            if r["used"] is not None:
                n["used"][key] = n["used"].get(key, Decimal(0)) + r["used"]
    out = sorted(by_night.values(), key=lambda n: n["night"], reverse=True)[:nights]
    for n in out:
        n["total"] = sum(n["used"].values(), Decimal(0)) if n["used"] else None
    return out


def price_per_kg(db: Session) -> Decimal | None:
    row = db.execute(text("""
        SELECT p.total_price, p.qty, p.unit::text AS unit FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE i.name = 'Cooking Gas' AND p.deleted_at IS NULL AND p.total_price > 0
         ORDER BY p.purchase_date DESC, p.id DESC LIMIT 1
    """)).mappings().first()
    if not row:
        return None
    cylinders = Decimal(str(row["qty"])) / FULL_KG if row["unit"] == "kg" else Decimal(str(row["qty"]))
    return Decimal(str(row["total_price"])) / (cylinders * FULL_KG) if cylinders else None


def page_data(db: Session) -> dict:
    st = cylinder_state(db)
    now = business_now()
    tonight = st["tonight"]
    per_kg = price_per_kg(db)
    nights = nightly(st["readings"], CHART_NIGHTS)
    done_nights = [n for n in nights if n["night"] < tonight or len(n["used"]) == len(CYLINDERS)]
    last_night = done_nights[0] if done_nights and done_nights[0]["night"] >= tonight - datetime.timedelta(days=1) else None

    rates = [c["rate"] for c in st["cylinders"] if c["rate"]]
    avg = sum(rates, Decimal(0)) if rates else None
    since = now.date() - datetime.timedelta(days=AVG_NIGHTS)
    sales = db.execute(text("""
        SELECT (SELECT coalesce(sum(qty), 0) FROM item_sales WHERE sale_date > :since) +
               (SELECT coalesce(sum(oi.quantity), 0) FROM orders o JOIN order_items oi ON oi.order_id = o.id
                 WHERE o.channel IN ('zomato', 'swiggy') AND (o.placed_at AT TIME ZONE 'Asia/Kolkata')::date > :since) AS dishes,
               (SELECT coalesce(sum(net_sales), 0) FROM daily_channel_sales WHERE business_date > :since) AS revenue,
               (SELECT count(DISTINCT sale_date) FROM item_sales WHERE sale_date > :since) AS days
    """), {"since": since}).mappings().first()
    days = sales["days"] or 0
    per_dish = pct_sales = None
    if avg and per_kg and days:
        cost_day = avg * per_kg
        if sales["dishes"]:
            per_dish = cost_day / (Decimal(str(sales["dishes"])) / days)
        if sales["revenue"]:
            pct_sales = cost_day / (Decimal(str(sales["revenue"])) / days) * 100

    month_start = now.date().replace(day=1)
    bills = db.execute(text("""
        SELECT count(*) AS n, coalesce(sum(CASE WHEN p.unit::text = 'kg' THEN p.qty / :full ELSE p.qty END), 0) AS cylinders,
               coalesce(sum(p.total_price), 0) AS spend
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE i.name = 'Cooking Gas' AND p.deleted_at IS NULL AND p.purchase_date >= :ms
    """), {"ms": month_start, "full": FULL_KG}).mappings().first()
    used_month = sum((r["used"] for rows in st["readings"].values() for r in rows
                      if r["used"] is not None and r["night"] >= month_start), Decimal(0))
    nightly_rows = [r for rows in st["readings"].values() for r in rows]
    first_night = min((r["night"] for r in nightly_rows if r["night"] >= month_start), default=None)

    soonest = min((c for c in st["cylinders"] if c["days_left"] is not None), key=lambda c: c["days_left"], default=None)
    logged = [c for c in st["cylinders"] if c["logged_tonight"]]
    return {
        **st, "nights": nights, "last_night": last_night, "per_kg": per_kg, "avg": avg,
        "per_dish": per_dish, "pct_sales": pct_sales, "soonest": soonest, "logged": logged,
        "bills": bills, "bills_kg": Decimal(str(bills["cylinders"])) * FULL_KG, "used_month": used_month,
        "first_night": first_night, "month_start": month_start,
        "chart": list(reversed(nights)), "chart_max": max([n["total"] or 0 for n in nights] + [Decimal(1)]),
    }


def tonight_payload(db: Session) -> dict:
    """For the staff app's Gas tab."""
    st = cylinder_state(db)
    cyls = []
    for c in st["cylinders"]:
        prev = c["rows"][-2] if c["logged_tonight"] and len(c["rows"]) > 1 else (None if c["logged_tonight"] else c["last"])
        cyls.append({
            "key": c["key"], "label": c["label"],
            "last_gross": float(prev["gross_kg"]) if prev else None,
            "last_at": prev["recorded_at"].isoformat() if prev else None,
            "last_stale": bool(prev) and (business_now() - prev["recorded_at"]).total_seconds() / 3600 > STALE_HOURS,
            "logged": ({"gross_kg": float(c["logged_tonight"]["gross_kg"]), "is_new": c["logged_tonight"]["is_new_cylinder"],
                        "by": c["logged_tonight"]["by_name"], "at": c["logged_tonight"]["recorded_at"].isoformat()}
                       if c["logged_tonight"] else None),
        })
    return {"night": st["tonight"].isoformat(), "tare_kg": float(TARE_KG), "full_kg": float(FULL_KG),
            "reminder": {"hour": REMINDER_HOUR, "minute": REMINDER_MINUTE},
            "cylinders": cyls, "done": all(c["logged"] for c in cyls)}


def save_readings(db: Session, readings: list[dict], user_id: int) -> tuple[dict, list[dict]]:
    """Insert tonight's reading per cylinder, or correct it if one was already
    logged tonight. Returns (summary, low) -- low = cylinders about to run out.
    Caller commits."""
    tonight = count_day(business_now())
    current = {c["key"]: c for c in cylinder_state(db)["cylinders"]}
    for r in readings:
        key = r["cylinder"]
        existing = current[key]["logged_tonight"]
        params = {"g": r["gross_kg"], "t": TARE_KG, "n": bool(r.get("is_new")), "u": user_id}
        if existing:
            db.execute(text("UPDATE gas_readings SET gross_kg = :g, is_new_cylinder = :n, recorded_by = :u WHERE id = :id"),
                       {**params, "id": existing["id"]})
        else:
            db.execute(text("""
                INSERT INTO gas_readings (cylinder, cylinder_role, gross_kg, tare_kg, is_new_cylinder, recorded_by)
                VALUES (:c, 'in_use', :g, :t, :n, :u)
            """), {**params, "c": key})
    db.flush()
    after = cylinder_state(db)
    summary, low = [], []
    for c in after["cylinders"]:
        lt = c["logged_tonight"]
        row = {"key": c["key"], "label": c["label"],
               "used": float(lt["used"]) if lt and lt["used"] is not None else None,
               "left": float(c["left"]) if c["left"] is not None else None,
               "days_left": float(c["days_left"]) if c["days_left"] is not None else None}
        summary.append(row)
        if c["days_left"] is not None and c["days_left"] < LOW_DAYS:
            low.append(row)
    return {"night": tonight.isoformat(), "cylinders": summary, "low": [r["key"] for r in low]}, low
