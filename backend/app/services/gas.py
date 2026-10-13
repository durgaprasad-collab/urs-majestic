"""Gas: two cylinders (Tandoor, Kitchen) weighed by staff every night at
12:30 AM in the staff app.

Staff type the scale reading (cylinder included); gas on hand is
gross - TARE_KG. Use between two readings of the same cylinder is the drop
in weight. The staff "new cylinder" tick is ignored (owner, 12 Oct 2026:
staff can't use it reliably): a reading more than SWAP_RISE_KG heavier
than the one before means a cylinder was swapped; that night has no usage
figure (it isn't counted in the daily rate) and the chain restarts. A reading belongs to the business night it closes:
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
SWAP_RISE_KG = Decimal("2")  # heavier than the last reading by more than this = swapped
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
    """Fill rows[i]["used"] / ["hours"] / ["swap"] for one cylinder's
    chronological readings (each with recorded_at and net). Use is the drop
    between readings; a rise of more than SWAP_RISE_KG is a swap (no figure)."""
    for r in rows:
        r["swap"] = False
    for prev, cur in zip(rows, rows[1:]):
        cur["hours"] = (cur["recorded_at"] - prev["recorded_at"]).total_seconds() / 3600
        if cur["net"] > prev["net"] + SWAP_RISE_KG:
            cur["swap"] = True
            continue
        if cur["hours"] > STALE_HOURS:
            continue
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
        swaps = [r for r in rows if r.get("swap")]
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


ROTATION_SLACK_KG = Decimal("0.5")  # scale noise allowed in the rotation check
HIGH_FACTOR = Decimal("1.4")        # a night this far above the others is flagged


def consolidated(readings: dict[str, list[dict]]) -> list[dict]:
    """Gas used by the whole kitchen per night, oldest first.

    used = gas on hand last night + new cylinders x FULL_KG - gas on hand tonight,
    so swap nights count too. The rotation (owner, 13 Oct 2026): the heaviest
    cylinder sits on the Tandoor; when the Kitchen one runs out, the Kitchen
    takes the Tandoor's cylinder and the Tandoor gets the new one. So a
    Kitchen swap whose new reading is no heavier than the Tandoor's last one is
    the Tandoor's cylinder moving over (one new cylinder in, even if the
    Tandoor's own reading barely moved); a Kitchen reading heavier than that
    breaks the rotation and is flagged. The Tandoor / Kitchen split assumes the
    move happened before service, so it is an estimate.
    """
    by_night: dict[datetime.date, dict] = {}
    for key, rows in readings.items():
        for r in rows:
            by_night.setdefault(r["night"], {})[key] = r  # last reading of the night wins
    keys = [k for k, _ in CYLINDERS]
    nights = [{"night": n, "rows": rows} for n, rows in sorted(by_night.items()) if all(k in rows for k in keys)]
    for prev, cur in zip(nights, nights[1:]):
        p, c = prev["rows"], cur["rows"]
        if any((c[k]["recorded_at"] - p[k]["recorded_at"]).total_seconds() / 3600 > STALE_HOURS for k in keys):
            continue
        tp, kp, tn, kn = p["tandoor"]["net"], p["kitchen"]["net"], c["tandoor"]["net"], c["kitchen"]["net"]
        t_swap = tn > tp + SWAP_RISE_KG
        k_swap = kn > kp + SWAP_RISE_KG
        k_inherit = k_swap and kn <= tp + ROTATION_SLACK_KG
        fitted = (1 if k_swap else 0) + (1 if t_swap and not k_inherit else 0)
        if k_inherit:
            split = {"tandoor": FULL_KG - tn, "kitchen": kp + tp - kn}
            returned = kp
        else:
            split = {"tandoor": (tp + FULL_KG if t_swap else tp) - tn, "kitchen": (kp + FULL_KG if k_swap else kp) - kn}
            returned = kp if k_swap else (tp if t_swap else None)
        used = tp + kp + fitted * FULL_KG - tn - kn
        cur.update({
            "used": max(used, Decimal(0)), "fitted": fitted, "returned": returned,
            "split": {k: max(v, Decimal(0)) for k, v in split.items()},
            "check": ("rotation" if k_inherit else ("broken" if k_swap else None)),
        })
    for n in nights:
        n["on_hand"] = sum((max(n["rows"][k]["net"], Decimal(0)) for k in keys), Decimal(0))
        n.setdefault("used", None)
        n.setdefault("fitted", 0)
        n.setdefault("check", None)
        n["high"] = False
    # "Too high": well above the other nights around it (needs two to compare).
    scored = [n for n in nights if n["used"] is not None]
    for n in scored:
        others = [o["used"] for o in scored if o is not n and abs((o["night"] - n["night"]).days) <= CHART_NIGHTS]
        if len(others) >= 2 and n["used"] > HIGH_FACTOR * (sum(others, Decimal(0)) / len(others)):
            n["high"] = True
    return nights


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
    all_nights = consolidated(st["readings"])
    nights = list(reversed(all_nights[-CHART_NIGHTS:]))  # newest first
    first_night = all_nights[0]["night"] if all_nights else None

    sales = {r["d"]: Decimal(str(r["net"])) for r in db.execute(text("""
        SELECT business_date AS d, sum(net_sales) AS net FROM daily_channel_sales
         WHERE business_date >= :since GROUP BY 1
    """), {"since": tonight - datetime.timedelta(days=CHART_NIGHTS + 1)}).mappings()}
    for n in nights:
        n["sales"] = sales.get(n["night"])
        n["cost"] = n["used"] * per_kg if (n["used"] is not None and per_kg) else None
        n["pct"] = n["cost"] / n["sales"] * 100 if (n["cost"] is not None and n["sales"]) else None

    scored = [n for n in nights if n["used"] is not None]
    last_night = scored[0] if scored and scored[0]["night"] >= tonight - datetime.timedelta(days=1) else None
    week = [n for n in scored if n["night"] > tonight - datetime.timedelta(days=AVG_NIGHTS + 1)]
    good = [n for n in week if not n["high"]]
    avg = sum((n["used"] for n in good), Decimal(0)) / len(good) if good else None
    left_out = [n for n in week if n["high"]]
    week_sales = [n["sales"] for n in good if n["sales"]]
    pct_sales = (avg * per_kg) / (sum(week_sales, Decimal(0)) / len(week_sales)) * 100 if (avg and per_kg and week_sales) else None
    sales_day = sum(week_sales, Decimal(0)) / len(week_sales) if week_sales else None

    # Gas on hand and which cylinder runs out first (rate from the split estimate).
    cyls = st["cylinders"]
    on_hand = sum((c["left"] for c in cyls if c["left"] is not None), Decimal(0)) if all(c["left"] is not None for c in cyls) else None
    for c in cyls:
        est = [n["split"][c["key"]] for n in good if n.get("split")]
        c["est_rate"] = sum(est, Decimal(0)) / len(est) if est else None
        c["est_days"] = c["left"] / c["est_rate"] if (c["left"] is not None and c["est_rate"]) else None
        c["fitted_night"] = night_of(c["fitted"]) if c["fitted"] else None
    soonest = min((c for c in cyls if c["est_days"] is not None), key=lambda c: c["est_days"], default=None)

    month_start = now.date().replace(day=1)
    bills = db.execute(text("""
        SELECT count(*) AS n, coalesce(sum(CASE WHEN p.unit::text = 'kg' THEN p.qty / :full ELSE p.qty END), 0) AS cylinders,
               coalesce(sum(p.total_price), 0) AS spend
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE i.name = 'Cooking Gas' AND p.deleted_at IS NULL AND p.purchase_date >= :ms
    """), {"ms": month_start, "full": FULL_KG}).mappings().first()
    bought_since = None
    if first_night:
        # Bills dated after the first consolidated night (the first night has no figure).
        bought_since = db.execute(text("""
            SELECT coalesce(sum(CASE WHEN p.unit::text = 'kg' THEN p.qty / :full ELSE p.qty END), 0)
              FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
             WHERE i.name = 'Cooking Gas' AND p.deleted_at IS NULL AND p.purchase_date > :d
        """), {"d": first_night, "full": FULL_KG}).scalar()
    fitted_since = sum(n["fitted"] for n in all_nights)
    month = [n for n in all_nights if n["used"] is not None and n["night"] >= month_start]
    used_month = sum((n["used"] for n in month), Decimal(0))
    returned = [n["returned"] for n in all_nights if n.get("returned") is not None][-6:]
    returned_avg = sum(returned, Decimal(0)) / len(returned) if returned else None

    logged = [c for c in cyls if c["logged_tonight"]]
    chart = list(reversed(nights))
    return {
        **st, "nights": nights, "last_night": last_night, "per_kg": per_kg, "avg": avg, "left_out": left_out,
        "pct_sales": pct_sales, "sales_day": sales_day, "soonest": soonest, "logged": logged, "on_hand": on_hand,
        "bills": bills, "bills_kg": Decimal(str(bills["cylinders"])) * FULL_KG, "used_month": used_month,
        "month_first": month[0]["night"] if month else None, "month_start": month_start,
        "first_night": first_night, "bought_since": Decimal(str(bought_since or 0)), "fitted_since": fitted_since,
        "returned": returned, "returned_avg": returned_avg,
        "chart": chart, "chart_max": max([n["used"] or 0 for n in chart] + [Decimal(1)]),
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
