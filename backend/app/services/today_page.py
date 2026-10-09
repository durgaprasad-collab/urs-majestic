"""Data for the Today page body (templates/today.html), below the pulse strip.

One break-even definition everywhere: target_engine's monthly break-even
(fixed costs / contribution margin) spread over the days in the month — the
same number the pulse strip shows.
"""
import calendar
import datetime
import decimal
import math

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import target_engine, task_engine

D = decimal.Decimal
TASKS_SHOWN = 8
TASKS_PER_ROLE = 3


def _f(x) -> float:
    return float(x or 0)


def _daily_targets(db: Session, day: datetime.date, mtd: float) -> tuple[float, float, dict]:
    month_start = day.replace(day=1)
    dim = calendar.monthrange(day.year, day.month)[1]
    t = target_engine.compute(db, mtd=D(str(mtd)), reporting_date=day, days_elapsed=(day - month_start).days + 1)
    return _f(t["break_even"]) / dim, _f(t["operating"]) / dim, t


def _pick_tasks(open_tasks: list[dict]) -> list[dict]:
    """Top tasks by score, at most TASKS_PER_ROLE per role, TASKS_SHOWN total."""
    shown, per_role = [], {}
    for task in open_tasks:
        if per_role.get(task["role"], 0) >= TASKS_PER_ROLE:
            continue
        shown.append(task)
        per_role[task["role"]] = per_role.get(task["role"], 0) + 1
        if len(shown) == TASKS_SHOWN:
            break
    return shown


def build_today(db: Session, reporting_date: datetime.date | None = None) -> dict:
    latest = db.execute(text("SELECT max(business_date) FROM daily_channel_sales")).scalar()
    if latest is None:
        return {"has_data": False}
    day = min(reporting_date or latest, latest)
    available = db.execute(text(
        "SELECT DISTINCT business_date FROM daily_channel_sales ORDER BY 1 DESC LIMIT 60")).scalars().all()

    rows = db.execute(text("""
        SELECT business_date d, channel, sum(net_sales) net
        FROM daily_channel_sales
        WHERE business_date > :day - 35 AND business_date <= :day
        GROUP BY 1, 2
    """), {"day": day}).mappings().all()
    by_day: dict = {}
    for r in rows:
        day_row = by_day.setdefault(r["d"], {"petpooja": 0.0, "zomato": 0.0, "swiggy": 0.0})
        day_row[r["channel"]] = day_row.get(r["channel"], 0.0) + _f(r["net"])

    def tot(d):
        v = by_day.get(d)
        return sum(v.values()) if v else 0.0

    month_start = day.replace(day=1)
    mtd = sum(tot(d) for d in by_day if d >= month_start)
    be, target, t = _daily_targets(db, day, mtd)
    prev_month_end = month_start - datetime.timedelta(days=1)
    be_prev, target_prev, _ = _daily_targets(db, prev_month_end, sum(tot(d) for d in by_day if d.month == prev_month_end.month))

    def be_for(d):
        return be if d >= month_start else be_prev

    # ── Verdict ──
    today_total = tot(day)
    gap = today_total - be
    lw = day - datetime.timedelta(days=7)
    ch_today = by_day.get(day, {"petpooja": 0, "zomato": 0, "swiggy": 0})
    ch_lw = by_day.get(lw, {"petpooja": 0, "zomato": 0, "swiggy": 0})
    week_start = day - datetime.timedelta(days=day.weekday())
    week_days = [week_start + datetime.timedelta(days=i) for i in range(min(4, day.weekday() + 1))]
    week_days = [d for d in week_days if d in by_day]
    week_ok = [d for d in week_days if tot(d) >= be_for(d)]
    best = db.execute(text("""
        SELECT s.item_name, sum(s.qty) q, sum(s.revenue) / nullif(sum(s.qty), 0) price
        FROM item_sales s JOIN menu_items m ON m.name = s.item_name
        WHERE m.is_food AND s.sale_date > :day - 7 AND s.sale_date <= :day
        GROUP BY 1 ORDER BY 2 DESC LIMIT 1
    """), {"day": day}).mappings().first()
    close_gap = None
    if gap < 0 and best and _f(best["price"]) > 0:
        close_gap = {"n": math.ceil(-gap / _f(best["price"])), "item": best["item_name"]}

    def pct(a, b):
        return (a / b - 1) * 100 if b else None

    verdict = {
        "total": today_total, "be": be, "gap": gap, "target": target,
        "walk": ch_today["petpooja"], "zomato": ch_today["zomato"], "swiggy": ch_today["swiggy"],
        "walk_lw": ch_lw["petpooja"], "zomato_lw": ch_lw["zomato"],
        "vs_lw": pct(today_total, tot(lw)), "walk_vs_lw": pct(ch_today["petpooja"], ch_lw["petpooja"]),
        "zomato_vs_lw": pct(ch_today["zomato"], ch_lw["zomato"]),
        "week_ok": len(week_ok), "week_n": len(week_days),
        "week_ok_days": [(d, tot(d)) for d in week_ok],
        "close_gap": close_gap,
        "fixed": _f(t["fixed_expenses"]), "margin_pct": _f(t["margin_pct"]),
        "days_in_month": calendar.monthrange(day.year, day.month)[1],
    }

    # ── 21-day bars ──
    span = [day - datetime.timedelta(days=i) for i in range(20, -1, -1)]
    bars = [{
        "d": d.isoformat(), "dow": d.strftime("%a"), "label": d.strftime("%d %b"),
        "walk": round(by_day.get(d, {}).get("petpooja", 0)), "zomato": round(by_day.get(d, {}).get("zomato", 0)),
        "swiggy": round(by_day.get(d, {}).get("swiggy", 0)), "be": round(be_for(d)),
        "target": round(target if d >= month_start else target_prev),
    } for d in span]
    wd = [b for b in bars if datetime.date.fromisoformat(b["d"]).isoweekday() <= 4]
    we = [b for b in bars if datetime.date.fromisoformat(b["d"]).isoweekday() >= 5]
    ok = lambda b: b["walk"] + b["zomato"] + b["swiggy"] >= b["be"]  # noqa: E731
    pattern = {"wd_ok": sum(map(ok, wd)), "wd_n": len(wd), "we_ok": sum(map(ok, we)), "we_n": len(we)}

    # ── Month cumulative ──
    dim = calendar.monthrange(day.year, day.month)[1]
    cum, c = [], 0.0
    for i in range(1, day.day + 1):
        c += tot(month_start.replace(day=i))
        cum.append(round(c))
    month = {
        "cum": cum, "be": round(be), "target": round(target), "days": dim, "label": day.strftime("%B"),
        "ahead": c - be * day.day, "achieved": (c - be * day.day) * _f(t["margin_pct"]) / 100,
        "goal": _f(t["desired_profit"]),
    }
    remaining = dim - day.day
    month["need_per_day"] = (
        (month["goal"] - month["achieved"]) / (_f(t["margin_pct"]) / 100) / remaining + be
        if remaining > 0 and _f(t["margin_pct"]) > 0 else None
    )

    # ── Weekday rhythm (last 28 days) ──
    rhythm = []
    for dow in range(1, 8):
        vals = [tot(d) for d in by_day if (day - d).days < 28 and d.isoweekday() == dow]
        avg = sum(vals) / len(vals) if vals else 0
        rhythm.append({"dow": calendar.day_abbr[dow - 1], "avg": avg, "ok": avg >= be})

    # ── Dishes: walk-in (Petpooja item report) + Zomato (order items) ──
    # Zomato order lines carry no per-dish price, so revenue is walk-in only.
    dishes = db.execute(text("""
        WITH w AS (
            SELECT s.item_name n,
                   sum(s.qty) FILTER (WHERE s.sale_date = :day) wq,
                   sum(s.revenue) FILTER (WHERE s.sale_date = :day) wr,
                   sum(s.qty) FILTER (WHERE s.sale_date = :day - 7) wq_lw,
                   sum(s.qty) FILTER (WHERE s.sale_date > :day - 7) w7
            FROM item_sales s JOIN menu_items m ON m.name = s.item_name
            WHERE m.is_food AND s.sale_date BETWEEN :day - 7 AND :day
            GROUP BY 1
        ), z AS (
            SELECT m.name n,
                   sum(oi.quantity) FILTER (WHERE (o.placed_at AT TIME ZONE 'Asia/Kolkata')::date = :day) zq,
                   sum(oi.quantity) FILTER (WHERE (o.placed_at AT TIME ZONE 'Asia/Kolkata')::date = :day - 7) zq_lw,
                   sum(oi.quantity) FILTER (WHERE (o.placed_at AT TIME ZONE 'Asia/Kolkata')::date > :day - 7) z7
            FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN menu_items m ON m.id = oi.menu_item_id
            WHERE o.channel = 'zomato' AND m.is_food
              AND (o.placed_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN :day - 7 AND :day
            GROUP BY 1
        )
        SELECT coalesce(w.n, z.n) AS name,
               coalesce(wq, 0)::int AS walk_q, coalesce(zq, 0)::int AS zomato_q, coalesce(wr, 0) AS walk_rev,
               (coalesce(wq_lw, 0) + coalesce(zq_lw, 0))::int AS last_week_q,
               (coalesce(w7, 0) + coalesce(z7, 0))::int AS week_q
        FROM w FULL JOIN z ON w.n = z.n
        WHERE coalesce(wq, 0) + coalesce(zq, 0) > 0
        ORDER BY coalesce(wq, 0) + coalesce(zq, 0) DESC, coalesce(wr, 0) DESC
    """), {"day": day}).mappings().all()
    dishes = [dict(r, total_q=r["walk_q"] + r["zomato_q"]) for r in dishes]
    dish_summary = {
        "count": len(dishes),
        "walk_q": sum(d["walk_q"] for d in dishes),
        "zomato_q": sum(d["zomato_q"] for d in dishes),
        "walk_rev": sum(_f(d["walk_rev"]) for d in dishes),
        "max_q": max((d["total_q"] for d in dishes), default=1),
    }

    # ── Running out (current stock, negative lines excluded) ──
    runout = db.execute(text("""
        SELECT name, on_hand_qty, unit, stock_days_cover_left cover FROM v_ingredient_reorder_forecast
        WHERE is_active AND on_hand_qty >= 0 AND stock_days_cover_left IS NOT NULL AND stock_days_cover_left < 5
        ORDER BY stock_days_cover_left LIMIT 7
    """)).mappings().all()
    negative = db.execute(text(
        "SELECT count(*) FROM v_ingredient_reorder_forecast WHERE is_active AND on_hand_qty < 0")).scalar() or 0

    tasks = task_engine.get_tasks(db, day if day != latest else None)
    return {
        "has_data": True, "day": day, "latest": latest, "available": available,
        "verdict": verdict, "bars": bars, "pattern": pattern, "month": month, "rhythm": rhythm,
        "dishes": dishes, "dish_summary": dish_summary,
        "runout": [dict(r) for r in runout], "negative_stock": negative,
        "tasks": tasks, "tasks_shown": _pick_tasks(tasks["open"]),
        "role_labels": task_engine.ROLE_LABELS,
    }
