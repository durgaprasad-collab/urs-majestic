"""Eagle-eye shell data: the six pulse tiles, sidebar badges and data-freshness
rows shown on every admin page (rendered by base.html / _pulse.html).

Targets (break-even / operating) come from target_engine, so the strip always
agrees with the Daily Brief and Business Settings. Everything else is a handful
of read-only aggregates. The DB is remote (~70ms per round-trip) and this runs
on every page view, so the result is cached in-process for a few minutes.
"""
import calendar
import datetime
import decimal
import logging
import threading
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.database import SessionLocal
from app.services import business_settings as bs
from app.services import target_engine

log = logging.getLogger(__name__)
D = decimal.Decimal

CACHE_SECONDS = 300
LEAK_COST_PCT = 40      # a dish is a "leak" above this food-cost %
LEAK_TARGET_PCT = 35    # leak value = cost above this % x qty sold
LEAK_MIN_QTY = 10       # ignore dishes sold fewer times than this in 30 days
ZOMATO_HIT_ORDERS = 20  # Zomato orders in 60 days that make a dish a "Zomato hit"
STALE_CHANNEL_DAYS = 3  # channel data older than this (vs newest data) is stale

_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def get_shell_context() -> dict | None:
    """Pulse/badge/freshness payload for base.html. Never raises and never makes
    a page wait once warm: a stale copy is served while a background thread
    rebuilds it. Only the very first call after a restart builds inline."""
    age = time.monotonic() - _cache["at"]
    if _cache["data"] is not None:
        if age >= CACHE_SECONDS:
            _refresh_async()
        return _cache["data"]
    _refresh()
    return _cache["data"]


def _refresh() -> None:
    if not _lock.acquire(blocking=_cache["data"] is None):
        return  # a refresh is already running; keep serving the current copy
    try:
        db = SessionLocal()
        try:
            _cache.update(at=time.monotonic(), data=_build(db))
        finally:
            db.close()
    except Exception:
        log.exception("shell pulse build failed")
        # Back off instead of retrying on every page view.
        _cache["at"] = time.monotonic()
    finally:
        _lock.release()


def _refresh_async() -> None:
    if not _lock.locked():
        threading.Thread(target=_refresh, name="shell-pulse", daemon=True).start()


def invalidate() -> None:
    _cache.update(at=0.0, data=None)


def _f(x) -> float:
    return float(x or 0)


def _status(cond_bad: bool, cond_warn: bool) -> str:
    return "bad" if cond_bad else ("warn" if cond_warn else "good")


def _build(db: Session) -> dict:
    today = business_today()

    # ── 1. Sales: newest business day, its channels, last week same weekday,
    #       month-to-date, last 7 days — one round-trip.
    sales = db.execute(text("""
        WITH asof AS (SELECT max(business_date) AS d FROM daily_channel_sales),
        day AS (
            SELECT business_date, channel, sum(net_sales) net, sum(orders) orders,
                   sum(gross_order_value) gross, sum(restaurant_discount) rdisc
            FROM daily_channel_sales, asof
            WHERE business_date >= date_trunc('month', asof.d)::date - 7 AND business_date <= asof.d
            GROUP BY 1, 2
        )
        SELECT (SELECT d FROM asof) AS asof, business_date, channel, net, orders, gross, rdisc FROM day
    """)).mappings().all()
    asof = sales[0]["asof"] if sales else None
    if asof is None:
        return {"has_data": False}

    month_start = asof.replace(day=1)
    days_in_month = calendar.monthrange(asof.year, asof.month)[1]
    by_day: dict = {}
    for r in sales:
        by_day.setdefault(r["business_date"], {})[r["channel"]] = r

    def day_total(d):
        return sum(_f(r["net"]) for r in by_day.get(d, {}).values())

    def ch(d, name):
        r = by_day.get(d, {}).get(name)
        return _f(r["net"]) if r else 0.0

    today_total = day_total(asof)
    last_week = asof - datetime.timedelta(days=7)
    mtd_days = [d for d in by_day if d >= month_start]
    mtd = sum(day_total(d) for d in mtd_days)
    days_elapsed = (asof - month_start).days + 1

    # ── 2. Targets from the canonical engine (Business Settings).
    targets = target_engine.compute(db, mtd=D(str(mtd)), reporting_date=asof, days_elapsed=days_elapsed)
    be_month = _f(targets["break_even"])
    op_month = _f(targets["operating"])
    be_day = be_month / days_in_month if be_month else 0.0
    op_day = op_month / days_in_month if op_month else 0.0
    margin = _f(targets["margin_pct"]) / 100
    fixed = _f(targets["fixed_expenses"])
    projected = _f(targets["projected_month_end"])
    profit = projected * margin - fixed if targets["computable"] else None
    goal = _f(targets["desired_profit"])
    # Profit already earned this month: contribution on MTD sales minus the
    # fixed costs that have accrued over the days elapsed (same margin model).
    remaining_days = days_in_month - days_elapsed
    achieved = mtd * margin - fixed * days_elapsed / days_in_month if targets["computable"] else None
    to_go = max(goal - achieved, 0.0) if achieved is not None else None
    need_per_day = None
    if to_go is not None and remaining_days > 0 and margin > 0:
        # Sales/day needed for the rest of the month to land exactly on the goal.
        need_per_day = (to_go + fixed * remaining_days / days_in_month) / margin / remaining_days
    margin_row = db.execute(text("""
        SELECT effective_from FROM business_settings WHERE setting_key = 'contribution_margin_pct'
        ORDER BY effective_from DESC, id DESC LIMIT 1
    """)).scalar()

    last7 = [asof - datetime.timedelta(days=i) for i in range(7)]
    last7_total = sum(day_total(d) for d in last7)
    last7_above_be = sum(1 for d in last7 if be_day and day_total(d) >= be_day)

    walkin_m = sum(ch(d, "petpooja") for d in mtd_days)
    zomato_m = sum(ch(d, "zomato") for d in mtd_days)
    swiggy_m = sum(ch(d, "swiggy") for d in mtd_days)
    channel_m = walkin_m + zomato_m + swiggy_m
    zom_rows = [by_day[d]["zomato"] for d in mtd_days if "zomato" in by_day[d]]
    zom_gross = sum(_f(r["gross"]) for r in zom_rows)
    zom_disc = sum(_f(r["rdisc"]) for r in zom_rows)
    zom_orders = sum(int(r["orders"] or 0) for r in zom_rows)

    def pct(part, whole):
        return round(100 * part / whole, 1) if whole else 0.0

    # ── 3. Margin leaks (last 30 days of POS item sales x recipe cost).
    # Priced at what each dish actually billed for in Petpooja (revenue / qty),
    # not menu_items.price, which lags behind POS price changes.
    leaks = db.execute(text("""
        WITH s AS (
            SELECT item_name, sum(qty) q, sum(revenue) / nullif(sum(qty), 0) AS sold_at FROM item_sales
            WHERE sale_date > :asof - 30 AND sale_date <= :asof GROUP BY 1
        ), z AS (
            SELECT oi.menu_item_id, count(*) n FROM order_items oi JOIN orders o ON o.id = oi.order_id
            WHERE o.channel = 'zomato' AND o.placed_at > now() - interval '60 days' GROUP BY 1
        ), priced AS (
            SELECT c.name, c.price AS menu_price, s.sold_at, s.q, coalesce(z.n, 0) zomato_orders,
                   100 * c.cost_with_fuel / s.sold_at AS pct
            FROM s JOIN v_menu_item_full_cost c ON c.name = s.item_name
            LEFT JOIN z ON z.menu_item_id = c.id
            WHERE c.is_food AND s.sold_at > 0 AND c.cost_with_fuel IS NOT NULL
        )
        SELECT *, (pct - :target) / 100 * sold_at * q AS leak
        FROM priced
        WHERE pct > :limit AND q >= :minq
        ORDER BY leak DESC
    """), {"asof": asof, "target": LEAK_TARGET_PCT, "limit": LEAK_COST_PCT, "minq": LEAK_MIN_QTY}).mappings().all()
    leak_rows = [{
        "name": r["name"], "price": _f(r["sold_at"]), "menu_price": _f(r["menu_price"]),
        "pct": round(_f(r["pct"]), 1), "qty": int(r["q"]),
        "leak": _f(r["leak"]), "zomato_hit": int(r["zomato_orders"]) >= ZOMATO_HIT_ORDERS,
    } for r in leaks]
    # A menu price is stale when the price Petpooja bills on most days differs
    # from the table. (The plain average is skewed by occasional variant or
    # discount lines, so it would flag dishes whose regular price is right.)
    stale_prices = db.execute(text("""
        WITH d AS (
            SELECT item_name, sale_date, round(sum(revenue) / nullif(sum(qty), 0), 2) AS unit, sum(qty) q
            FROM item_sales WHERE sale_date > :asof - 30 AND sale_date <= :asof GROUP BY 1, 2
        ), modal AS (
            SELECT DISTINCT ON (item_name) item_name, unit, days
            FROM (SELECT item_name, unit, count(*) days, max(sale_date) last_day FROM d GROUP BY 1, 2) x
            ORDER BY item_name, days DESC, last_day DESC
        ), tot AS (SELECT item_name, sum(q) q, count(*) days FROM d GROUP BY 1)
        -- Only a dish with one clearly regular price (billed on at least half its
        -- selling days) can be called stale; dishes with variants bill all over.
        SELECT count(*) FROM modal JOIN tot USING (item_name) JOIN menu_items m ON m.name = modal.item_name
        WHERE m.is_active AND m.is_food AND tot.q >= :minq AND abs(modal.unit - m.price) >= 1
          AND modal.days * 2 >= tot.days
    """), {"asof": asof, "minq": LEAK_MIN_QTY}).scalar() or 0
    leak_total = sum(r["leak"] for r in leak_rows)

    # ── 4. Gas + oil vs sales (last complete month), last gas bill.
    prev_end = month_start - datetime.timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    go = db.execute(text("""
        SELECT
          sum(p.total_price) FILTER (WHERE i.name = 'Cooking Gas' AND p.purchase_date BETWEEN :ps AND :pe) gas,
          count(*)           FILTER (WHERE i.name = 'Cooking Gas' AND p.purchase_date BETWEEN :ps AND :pe) gas_n,
          sum(p.total_price) FILTER (WHERE i.name = 'Oil' AND p.purchase_date BETWEEN :ps AND :pe) oil,
          max(p.purchase_date) FILTER (WHERE i.name = 'Cooking Gas') last_gas,
          (SELECT sum(net_sales) FROM daily_channel_sales WHERE business_date BETWEEN :ps AND :pe) sales
        FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
        WHERE p.deleted_at IS NULL
    """), {"ps": prev_start, "pe": prev_end}).mappings().one()
    gas_pct = pct(_f(go["gas"]), _f(go["sales"]))
    oil_pct = pct(_f(go["oil"]), _f(go["sales"]))
    gas_age = (today - go["last_gas"]).days if go["last_gas"] else None

    # ── 5. Data-trust signals, badges and freshness — one round-trip.
    t = db.execute(text("""
        SELECT
          (SELECT count(*) FROM v_ingredient_reorder_forecast WHERE is_active AND on_hand_qty < 0) neg_stock,
          (SELECT count(*) FROM purchase_ledger_sync_issues WHERE resolved_at IS NULL) ledger_open,
          (SELECT count(*) FROM requisitions WHERE status = 'pending') req_pending,
          (SELECT unexplained_mismatches FROM v_data_trust) recon_open,
          (SELECT max(business_date) FROM daily_channel_sales WHERE channel = 'swiggy') swiggy_last,
          (SELECT max(business_date) FROM daily_channel_sales WHERE channel = 'zomato') zomato_last,
          (SELECT max(business_date) FROM daily_channel_sales WHERE channel = 'petpooja') petpooja_last,
          (SELECT max(uploaded_at) FROM upload_log WHERE succeeded AND channel = 'petpooja') petpooja_up,
          (SELECT max(uploaded_at) FROM upload_log WHERE succeeded AND channel = 'zomato') zomato_up,
          (SELECT max(uploaded_at) FROM upload_log WHERE succeeded AND channel = 'swiggy') swiggy_up,
          (SELECT count(*) FROM daily_channel_sales
             WHERE channel = 'petpooja' AND business_date > :asof - 30 AND orders > 0
               AND net_sales / orders > 3 * (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY net_sales / orders)
                                             FROM daily_channel_sales WHERE channel = 'petpooja' AND orders > 0
                                               AND business_date > :asof - 30)) bad_order_days
    """), {"asof": asof}).mappings().one()

    issues = []
    if t["neg_stock"]:
        issues.append({"key": "stock", "title": "Stock model", "value": f"{t['neg_stock']} negative",
                       "note": "Ingredients below zero, usually a g/kg mix-up in a recipe", "level": "bad",
                       "href": "/stock-log"})
    swiggy_lag = (asof - t["swiggy_last"]).days if t["swiggy_last"] else None
    if swiggy_lag is not None and swiggy_lag > STALE_CHANNEL_DAYS:
        issues.append({"key": "swiggy", "title": "Swiggy upload", "value": f"{swiggy_lag} days behind",
                       "note": f"Last data {t['swiggy_last']:%d %b}", "level": "bad", "href": "/upload"})
    if t["bad_order_days"]:
        issues.append({"key": "orders", "title": "Petpooja order counts", "value": f"{t['bad_order_days']} days look wrong",
                       "note": "Bill count far too low for the day's sales, so average bill is off",
                       "level": "warn", "href": "/data-reconciliation"})
    # Flag the stored margin when the data says something different, not by age.
    dm = bs.derived_margin(db, asof)
    margin_drifted = bool(dm and dm["drifted"])
    if margin_drifted:
        issues.append({"key": "settings", "title": "Contribution margin",
                       "value": f"{float(dm['stored']):.0f}% stored, data says {float(dm['pct']):.1f}%",
                       "note": f"Measured {dm['start']:%d %b}–{dm['end']:%d %b}. Drives every target; approve it in Business Settings",
                       "level": "warn", "href": "/business-settings#margin"})
    if stale_prices:
        issues.append({"key": "prices", "title": "Menu prices", "value": f"{stale_prices} out of date",
                       "note": "Petpooja bills a different price than the menu table, so food cost % there is off",
                       "level": "warn", "href": "/results"})
    if t["recon_open"]:
        issues.append({"key": "recon", "title": "Reconciliation", "value": f"{t['recon_open']} mismatches",
                       "note": "Channel totals disagree", "level": "bad", "href": "/reconciliation"})

    def fresh(label, last_day, uploaded):
        lag = (asof - last_day).days if last_day else None
        up = uploaded.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30))) if uploaded else None
        if lag is None:
            return {"label": label, "value": "never", "level": "bad"}
        if lag > STALE_CHANNEL_DAYS:
            return {"label": label, "value": f"{lag} days behind", "level": "bad"}
        value = f"Today {up:%H:%M}" if up and up.date() == today else (f"{up:%d %b}" if up else f"{last_day:%d %b}")
        return {"label": label, "value": value, "level": "warn" if lag > 1 else "good"}

    freshness = [
        fresh("Petpooja", t["petpooja_last"], t["petpooja_up"]),
        fresh("Zomato", t["zomato_last"], t["zomato_up"]),
        fresh("Swiggy", t["swiggy_last"], t["swiggy_up"]),
        {"label": "Gas bill", "value": f"{gas_age} days ago" if gas_age is not None else "never",
         "level": _status(gas_age is None or gas_age > 10, gas_age is not None and gas_age > 4)},
    ]

    walkin_today, zomato_today = ch(asof, "petpooja"), ch(asof, "zomato")
    walkin_lw, zomato_lw = ch(last_week, "petpooja"), ch(last_week, "zomato")

    return {
        "has_data": True,
        "asof": asof,
        "sales": {
            "total": today_total, "be": be_day, "target": op_day,
            "vs_be": today_total - be_day, "vs_target": today_total - op_day,
            "fill_pct": min(100, pct(today_total, op_day)), "be_pct": min(100, pct(be_day, op_day)),
            "status": "good" if today_total >= op_day else ("warn" if today_total >= be_day else "bad"),
            "walkin": walkin_today, "zomato": zomato_today,
            "walkin_vs_lw": (walkin_today / walkin_lw - 1) * 100 if walkin_lw else None,
            "zomato_lw": zomato_lw,
            "last7": last7_total, "last7_above_be": last7_above_be,
            "fixed": fixed, "margin_pct": _f(targets["margin_pct"]), "margin_set": margin_row,
        },
        "profit": {
            "value": profit, "goal": goal, "pct": pct(profit, goal) if profit is not None and goal else 0,
            "achieved": achieved, "to_go": to_go, "need_per_day": need_per_day, "remaining_days": remaining_days,
            "achieved_pct": min(100, max(0, pct(achieved, goal))) if achieved is not None and goal else 0,
            "projected_sales": projected, "daily_avg": _f(targets["daily_avg"]), "fixed": fixed,
            "variable": projected * (1 - margin), "margin_pct": _f(targets["margin_pct"]),
            "status": "bad" if profit is None or profit < 0 else ("good" if profit >= goal else "warn"),
        },
        "channels": {
            "walkin": walkin_m, "zomato": zomato_m, "swiggy": swiggy_m,
            "walkin_pct": pct(walkin_m, channel_m), "zomato_pct": pct(zomato_m, channel_m), "swiggy_pct": pct(swiggy_m, channel_m),
            "zomato_disc_pct": pct(zom_disc, zom_gross), "zomato_orders": zom_orders,
            "zomato_aov": zomato_m / zom_orders if zom_orders else 0, "month": asof,
        },
        "leaks": {
            "count": len(leak_rows), "total": leak_total, "rows": leak_rows,
            "zomato_hits": sum(1 for r in leak_rows if r["zomato_hit"]),
            "status": _status(leak_total > 5000, len(leak_rows) > 0),
        },
        "gas_oil": {
            "pct": round(gas_pct + oil_pct, 1), "gas_pct": gas_pct, "oil_pct": oil_pct,
            "gas": _f(go["gas"]), "gas_n": int(go["gas_n"] or 0), "oil": _f(go["oil"]),
            "month": prev_start, "last_gas": go["last_gas"], "gas_age": gas_age,
            "status": _status(gas_pct + oil_pct > 20, (gas_pct + oil_pct > 12) or (gas_age or 0) > 4),
        },
        "trust": {
            "issues": issues, "count": len(issues),
            "status": _status(any(i["level"] == "bad" for i in issues), bool(issues)),
        },
        "badges": {
            "requisitions": t["req_pending"] or 0,
            "stock": t["neg_stock"] or 0,
            "purchases": t["ledger_open"] or 0,
            "reconcile": t["recon_open"] or 0,
            "menu": len(leak_rows),
            "settings": margin_drifted,
        },
        "freshness": freshness,
    }
