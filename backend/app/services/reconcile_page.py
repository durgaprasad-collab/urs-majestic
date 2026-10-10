"""Reconcile: did the money that came in cover what went out?

Revenue from every channel against purchases (cash spent) and fixed costs,
with fixed costs spread evenly over the month so a part-month compares
fairly (the old ledger set a whole month's fixed costs against a few days of
sales). Break-even revenue per day comes from the target engine. Plus the
channel data check: uploads vs each channel's own totals, and freshness.
"""
import calendar
import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.services.recon import get_channel_status, get_daily_recon

MONTHS_SHOWN = 3
STALE_DAYS = 3   # a channel whose last data is older than this needs an upload
CHANNEL_LABEL = {"petpooja": "Dine-in (Petpooja)", "zomato": "Zomato", "swiggy": "Swiggy", "catering": "Catering"}


def _months(today: datetime.date) -> list[datetime.date]:
    first = today.replace(day=1)
    out = [first]
    for _ in range(MONTHS_SHOWN - 1):
        out.append((out[-1] - datetime.timedelta(days=1)).replace(day=1))
    return list(reversed(out))


def data_through(db: Session) -> datetime.date:
    """Last business day with dine-in sales uploaded (the day the page is 'as of')."""
    d = db.execute(text("SELECT max(business_date) FROM daily_channel_sales WHERE channel = 'petpooja'")).scalar()
    return d or business_today()


def month_view(db: Session, month_start: datetime.date, through: datetime.date) -> dict:
    from app.web.engine_routes import _calendar_month_ledger  # avoid import cycle at load

    days_in = calendar.monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=days_in)
    end = min(month_end, through)
    if end < month_start:
        end = month_start
    led = _calendar_month_ledger(db, month_start, month_end, end)
    days = len(led["rows"])
    fixed_month = led["fixed_total"]
    fixed_to_date = (fixed_month * days / days_in).quantize(Decimal("1"))
    target = led["target_total"] or Decimal(0)
    be_day = (target / days_in) if target else None
    revenue, purchases = led["revenue_total"], led["purchases_total"]
    return {
        "start": month_start, "end": end, "days": days, "days_in": days_in, "partial": end < month_end,
        "rows": led["rows"], "revenue": revenue, "purchases": purchases,
        "purchase_pct": float(purchases / revenue * 100) if revenue else None,
        "fixed_month": fixed_month, "fixed_to_date": fixed_to_date,
        "result": revenue - purchases - fixed_to_date,
        "be_to_date": (be_day * days) if be_day else None, "be_day": be_day,
        "vs_be": float(revenue / (be_day * days) * 100 - 100) if be_day and days else None,
        "chart_max": max([r["revenue"] for r in led["rows"]] + [r["purchases"] for r in led["rows"]]
                         + [be_day * Decimal("1.15") if be_day else Decimal(1)]),
    }


def categories(db: Session, m: dict) -> list[dict]:
    rows = db.execute(text("""
        SELECT coalesce(i.category, 'Other') AS cat, sum(p.total_price) AS rs
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.purchase_date BETWEEN :s AND :e
         GROUP BY 1 ORDER BY 2 DESC
    """), {"s": m["start"], "e": m["end"]}).mappings().all()
    top = [dict(r) for r in rows[:6]]
    rest = sum((r["rs"] for r in rows[6:]), Decimal(0))
    if rest:
        top.append({"cat": "Everything else", "rs": rest})
    for r in top:
        r["pct"] = float(r["rs"] / m["revenue"] * 100) if m["revenue"] else None
    return top


USE_LABEL = {"menu": "Menu", "others_personal": "Personal", "excluded_unidentified": "Excluded"}


def items(db: Session, months: list[dict]) -> list[dict]:
    """Item-wise breakdown: quantity and spend per item for each month shown.

    Quantity is in the item's own unit; a line whose unit can't be converted
    still counts in spend but is flagged so the total isn't silently short.
    """
    from app.services.purchases_page import to_item_unit

    keys = [m["start"].strftime("%Y-%m") for m in months]
    rows = db.execute(text("""
        SELECT p.ingredient_id, i.name, coalesce(i.category, 'Other') AS category, i.unit::text AS item_unit,
               i.pack_size_g, p.qty, p.unit::text AS unit, p.total_price, p.purchase_date,
               p.usage_type::text AS usage_type
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.purchase_date BETWEEN :s AND :e
    """), {"s": months[0]["start"], "e": business_today()}).mappings().all()
    acc: dict = {}
    for r in rows:
        key = r["purchase_date"].strftime("%Y-%m")
        if key not in keys:
            continue
        it = acc.setdefault((r["ingredient_id"], r["usage_type"]), {
            "id": r["ingredient_id"], "name": r["name"], "category": r["category"], "unit": r["item_unit"],
            "use": USE_LABEL.get(r["usage_type"], r["usage_type"]), "usage_type": r["usage_type"],
            "months": {k: {"qty": 0.0, "rs": Decimal(0), "n": 0} for k in keys},
            "qty": 0.0, "rs": Decimal(0), "n": 0, "unconverted": 0,
        })
        q = to_item_unit(float(r["qty"]), r["unit"], r["item_unit"], r["pack_size_g"])
        cell = it["months"][key]
        cell["rs"] += r["total_price"]
        cell["n"] += 1
        it["rs"] += r["total_price"]
        it["n"] += 1
        if q is None:
            it["unconverted"] += 1
        else:
            cell["qty"] += q
            it["qty"] += q
    out = list(acc.values())
    out.sort(key=lambda x: (-x["rs"], x["name"]))
    return out


def channels(db: Session, m: dict, through: datetime.date) -> list[dict]:
    status = {r["channel"]: r for r in get_channel_status(db)}
    daily = get_daily_recon(db)
    checked = {}
    for r in daily:
        c = checked.setdefault(r["channel"], {"ok": 0, "explained": 0, "open": 0})
        c["ok" if r["status"] == "OK" else ("explained" if r["status"] == "EXPLAINED" else "open")] += 1
    money = {r["channel"]: r for r in db.execute(text("""
        SELECT channel, sum(net_sales) AS rs, sum(orders) AS orders FROM daily_channel_sales
         WHERE business_date BETWEEN :s AND :e GROUP BY 1
    """), {"s": m["start"], "e": m["end"]}).mappings()}
    out = []
    for ch in ("petpooja", "zomato", "swiggy", "catering"):
        st, mo, ck = status.get(ch), money.get(ch), checked.get(ch, {"ok": 0, "explained": 0, "open": 0})
        if not st and not mo:
            continue
        last = st["latest_data_date"] if st else None
        lag = (through - last).days if last else None
        out.append({
            "channel": ch, "label": CHANNEL_LABEL.get(ch, ch.title()),
            "revenue": mo["rs"] if mo else Decimal(0), "orders": int(mo["orders"] or 0) if mo else 0,
            "last": last, "lag": lag,
            # Catering is occasional; it never goes "stale".
            "stale": ch != "catering" and lag is not None and lag > STALE_DAYS,
            **ck,
        })
    return out


def open_mismatches(db: Session) -> list[dict]:
    return [r for r in get_daily_recon(db) if r["status"] not in ("OK", "EXPLAINED")]


def page(db: Session, month_key: str | None) -> dict:
    through = data_through(db)
    months = _months(through)
    sel = next((m for m in months if m.strftime("%Y-%m") == month_key), months[-1])
    views = [month_view(db, m, through) for m in months]
    cur = next(v for v in views if v["start"] == sel)
    chans = channels(db, cur, through)
    return {"through": through, "months": views, "m": cur, "cats": categories(db, cur),
            "items": items(db, views),
            "channels": chans, "mismatches": open_mismatches(db),
            "stale": [c for c in chans if c["stale"]]}
