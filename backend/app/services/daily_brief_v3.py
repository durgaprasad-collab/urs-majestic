"""Daily Brief v3 — composition layer (requirements 1-11).

build_brief(db) assembles the whole page context. Because the database is
remote (per-query latency dominates page load), the data layer is deliberately
lean: shared primitives are computed ONCE and every dependent section (target,
trend, contribution) is derived in pure Python from a single sales-series read
rather than re-querying. That kills the duplicate calculations and keeps the
round-trip count low. Section builders below are pure functions of already-
fetched data — no builder issues its own N+1 query.

Design notes for the future modules named in the spec:
  * Website orders   -> add 'website' to _CHANNELS and feed daily_channel_sales.
  * Recipe costing   -> replace _FOOD_COST usage in _contribution().
  * Actual margin    -> set contribution['estimated'] = False downstream.
  * AI Morning Brief -> consume kpi_governance.registry() + this context dict.
Nothing here needs restructuring for those to plug in.
"""
import calendar
import datetime
import decimal
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.ceo_brief import get_summary, get_menu, get_actions
from app.services.kpi import get_channel_upload_status
from app.services.recon import get_data_trust
from app.services.kpi_governance import registry as kpi_registry

D = decimal.Decimal

# Priority ladder shared by Attention + GM Actions.
CRITICAL, HIGH, MEDIUM = 1, 2, 3
_PRI_LABEL = {CRITICAL: "CRITICAL", HIGH: "HIGH", MEDIUM: "MEDIUM"}


# ── shared data reads ────────────────────────────────────────────────────────
def _last_refresh(db: Session) -> datetime.datetime | None:
    return db.execute(text("SELECT max(uploaded_at) FROM daily_channel_sales")).scalar()


def _sales_series(db: Session, through: datetime.date) -> dict[datetime.date, decimal.Decimal]:
    """ONE query returning the daily net-sales series covering both the current
    month-to-date AND the last 7 days (whichever starts earlier). Target, trend
    and contribution are all derived from this in pure Python — no re-querying."""
    month_start = through.replace(day=1)
    start = min(month_start, through - datetime.timedelta(days=6))
    rows = db.execute(
        text(
            "SELECT business_date AS d, sum(net_sales) AS net "
            "FROM daily_channel_sales WHERE business_date >= :s AND business_date <= :e "
            "GROUP BY business_date"
        ),
        {"s": start, "e": through},
    ).mappings().all()
    return {r["d"]: D(str(r["net"] or 0)) for r in rows}


def _reporting_date_rows(db: Session, d: datetime.date) -> dict[str, dict]:
    """One query: every daily_channel_sales row for the reporting date, keyed by
    channel. Powers channel performance, the Swiggy-zero and discount rules."""
    rows = db.execute(
        text(
            "SELECT channel, net_sales, orders, gross_order_value, restaurant_discount "
            "FROM daily_channel_sales WHERE business_date = :d"
        ),
        {"d": d},
    ).mappings().all()
    return {r["channel"]: dict(r) for r in rows}


def _failed_rows_by_channel(db: Session) -> dict[str, int]:
    """One query: rows_failed on each channel's most-recent upload (>0 only)."""
    rows = db.execute(
        text(
            "SELECT DISTINCT ON (channel) channel, rows_failed "
            "FROM upload_log ORDER BY channel, uploaded_at DESC"
        )
    ).mappings().all()
    return {r["channel"]: r["rows_failed"] for r in rows if (r["rows_failed"] or 0) > 0}


# ── pure metrics derived from the sales series (no queries) ───────────────────
_FOOD_COST = D(str(settings.ASSUMED_FOOD_COST_PCT or 0))


def _target(through: datetime.date, mtd: decimal.Decimal) -> dict:
    """Monthly target vs month-to-date, pace and projection. Pure."""
    target = D(str(settings.MONTHLY_SALES_TARGET or 0))
    days_in_month = calendar.monthrange(through.year, through.month)[1]
    days_elapsed = through.day
    out: dict = {
        "target_set": target > 0,
        "target": target,
        "mtd": mtd,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "pace_marker_pct": round(100.0 * days_elapsed / days_in_month, 1),
        "month_label": through.strftime("%B %Y"),
    }
    if target > 0:
        expected = target * D(days_elapsed) / D(days_in_month)
        projection = (mtd / D(days_elapsed) * D(days_in_month)) if days_elapsed else D(0)
        out.update({
            "pct_of_target": float(mtd / target * 100),
            "expected_to_date": expected,
            "pace_delta": mtd - expected,
            "pace_pct": float((mtd - expected) / expected * 100) if expected else 0.0,
            "projection": projection,
            "projection_pct_of_target": float(projection / target * 100),
            "on_track": mtd >= expected,
        })
    return out


def _contribution(through, day_sales, mtd_sales, pct_vs_prior_day) -> dict:
    """Estimated contribution (flat food-cost assumption). Pure."""
    fc_pct = float(_FOOD_COST * 100)
    return {
        "as_of": through,
        "estimated": True,
        "food_cost_pct": fc_pct,
        "contribution_pct": 100.0 - fc_pct,
        "day_sales": day_sales,
        "day_contribution": day_sales * (1 - _FOOD_COST),
        "mtd_sales": mtd_sales,
        "mtd_contribution": mtd_sales * (1 - _FOOD_COST),
        "trend_pct": pct_vs_prior_day,
        "trend_dir": "flat" if pct_vs_prior_day is None else ("up" if pct_vs_prior_day >= 0 else "down"),
    }


# ── 1. Executive Summary ─────────────────────────────────────────────────────
def _executive_summary(summary: dict, target: dict | None, last_refresh, data_status: str) -> dict:
    exto = target.get("expected_to_date") if target and target.get("target_set") else None
    return {
        "reporting_date": summary["data_through"],
        "last_refresh": last_refresh,
        "data_status": data_status,
        "net_sales": summary["latest_day_sales"],
        "pct_vs_prior_day": summary["pct_vs_prior_day"],
        "pct_vs_same_weekday": summary["pct_vs_same_weekday"],
        "target_set": bool(target and target.get("target_set")),
        "monthly_target": target["target"] if target else D(0),
        "mtd_sales": target["mtd"] if target else D(0),
        "target_pct": target.get("pct_of_target") if target else None,
        "expected_mtd": exto,
        "variance": target.get("pace_delta") if target and target.get("target_set") else None,
    }


# ── 6. Seven-Day Trend (enhanced) — pure, from the sales series ───────────────
def _trend(series: dict[datetime.date, decimal.Decimal], through: datetime.date) -> dict:
    start = through - datetime.timedelta(days=6)
    peak = max((series.get(start + datetime.timedelta(days=i), D(0)) for i in range(7)), default=D(0))
    days = []
    for i in range(7):
        dt = start + datetime.timedelta(days=i)
        net = series.get(dt, D(0))
        days.append({
            "date": dt,
            "net": net,
            "has_data": dt in series,
            "pct": int(net / peak * 100) if peak > 0 else 0,
        })
    t = {"days": days, "peak": peak}
    have = [d for d in days if d["has_data"]]
    if not have:
        t.update({"high": None, "low": None, "avg": D(0), "streak_len": 0, "streak_dir": None})
        return t
    high = max(have, key=lambda d: d["net"])
    low = min(have, key=lambda d: d["net"])
    avg = sum((d["net"] for d in have), D(0)) / D(len(have))

    # Streak: consecutive day-over-day moves in the same direction at the tail.
    streak_dir, streak_len = None, 0
    for i in range(len(have) - 1, 0, -1):
        diff = have[i]["net"] - have[i - 1]["net"]
        d = "up" if diff > 0 else ("down" if diff < 0 else "flat")
        if d == "flat":
            break
        if streak_dir is None:
            streak_dir, streak_len = d, 1
        elif d == streak_dir:
            streak_len += 1
        else:
            break
    t.update({"high": high, "low": low, "avg": avg, "streak_len": streak_len, "streak_dir": streak_dir})
    return t


# ── 7. Channel Performance (+ Website placeholder) ───────────────────────────
# (label, summary-key, daily_channel_sales channel). Website has no channel yet.
_CHANNELS = [
    ("Counter", "counter_sales", "petpooja"),
    ("Zomato", "zomato_sales", "zomato"),
    ("Swiggy", "swiggy_sales", "swiggy"),
    ("Website", None, None),
]


def _channel_performance(summary: dict, rows: dict[str, dict]) -> list[dict]:
    out = []
    for label, skey, chan in _CHANNELS:
        if chan is None:
            # Future-ready: no integration yet -> "No sales", never a fake ₹0.
            out.append({"label": label, "value": None, "status": "not_connected", "note": "No sales"})
            continue
        if chan not in rows:
            # Expected an upload for the reporting date but none present.
            out.append({"label": label, "value": None, "status": "pending", "note": "Data pending"})
            continue
        net = summary.get(skey) or rows[chan]["net_sales"] or D(0)
        status = "zero" if (net or 0) == 0 else "ok"
        out.append({"label": label, "value": net, "status": status,
                    "note": "No sales" if status == "zero" else None})
    return out


# ── 8. Customer Section (gracefully degrading) ───────────────────────────────
def _rating(v: float) -> float | None:
    return v if v and v > 0 else None


def _customer(db: Session) -> dict:
    fb = db.execute(
        text(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE created_at >= now() - interval '7 days') AS new7, "
            "round(avg(rating)::numeric, 1) AS avg_rating "
            "FROM customer_feedback"
        )
    ).mappings().first() or {}
    awaiting = settings.REVIEWS_AWAITING_RESPONSE
    return {
        "google": _rating(settings.GOOGLE_RATING),
        "zomato": _rating(settings.ZOMATO_RATING),
        "swiggy": _rating(settings.SWIGGY_RATING),
        "counter_avg": fb.get("avg_rating"),
        "review_count": fb.get("total") or 0,
        "new_reviews": fb.get("new7") or 0,
        "awaiting_response": awaiting if awaiting is not None and awaiting >= 0 else None,
        "any_rating_connected": any(
            _rating(v) for v in (settings.GOOGLE_RATING, settings.ZOMATO_RATING, settings.SWIGGY_RATING)
        ),
    }


# ── 9. Operations ────────────────────────────────────────────────────────────
def _operations(channel_status: list[dict], failed: dict, data_trust: dict | None) -> dict:
    uploads = []
    for c in channel_status:
        uploads.append(
            {
                "channel": c["channel"],
                "latest_date": c["latest_date"],
                "is_stale": c["is_stale"],
                "days_behind": c["days_behind_freshest"],
            }
        )
    prep = settings.AVG_PREP_TIME_MIN
    return {
        "uploads": uploads,
        "recon_status": (data_trust or {}).get("indicator") or "DATA PENDING",
        "import_errors": failed,  # {channel: rows_failed}
        "import_error_total": sum(failed.values()) if failed else 0,
        "avg_prep_time": prep if prep and prep > 0 else None,
    }


# ── 4. Attention Required (max 5, Critical > High > Medium) ───────────────────
def _attention(summary, target, data_trust, rows, channel_status, failed, zero_count) -> list[dict]:
    items: list[dict] = []
    rep = summary["data_through"]

    def add(pri, icon, title, detail, link=None, link_label=None):
        items.append({"pri": pri, "sev": pri, "icon": icon, "title": title,
                      "detail": detail, "link": link, "link_label": link_label})

    # Critical — data can't be trusted / is incomplete
    if data_trust and (data_trust.get("unexplained_mismatches") or 0) > 0:
        n = data_trust["unexplained_mismatches"]
        add(CRITICAL, "🔴", f"Reconciliation failed — {n} unexplained mismatch(es)",
            "Numbers below may be wrong until this is reconciled.",
            "/data-reconciliation", "Reconcile")
    missing = [c["channel"] for c in channel_status
               if c["latest_date"] is None or c["latest_date"] < rep]
    for chan in missing:
        add(CRITICAL, "🔴", f"{chan.title()} upload missing",
            f"No {chan.title()} data for {rep:%d %b} — today's totals are incomplete.",
            "/upload", "Upload")

    # High — margin / channel exceptions worth acting on today
    if failed:
        tot = sum(failed.values())
        add(HIGH, "🟠", f"{tot} row(s) failed to import",
            "Some sales are missing from the totals. Re-check the source file.",
            "/data-reconciliation", "Review")
    if target and target.get("target_set") and target.get("pace_pct", 0) <= -10:
        add(HIGH, "🟠", f"Behind monthly pace by {abs(target['pace_pct']):.0f}%",
            f"₹{target['mtd']:,.0f} vs ₹{target['expected_to_date']:,.0f} expected. "
            f"Projected ₹{target['projection']:,.0f}.")
    policy = settings.DELIVERY_DISCOUNT_POLICY_PCT
    for chan in ("zomato", "swiggy"):
        r = rows.get(chan)
        if r and r.get("gross_order_value") and r["gross_order_value"] > 0:
            disc_pct = float(D(str(r["restaurant_discount"] or 0)) / D(str(r["gross_order_value"])) * 100)
            if disc_pct > policy:
                add(HIGH, "🟠", f"{chan.title()} funded discount {disc_pct:.0f}% — over {policy:.0f}% policy",
                    f"₹{r['restaurant_discount']:,.0f} funded on ₹{r['gross_order_value']:,.0f} gross.")
    # Swiggy uploaded for the day but did zero orders
    sw = rows.get("swiggy")
    if sw is not None and (sw.get("net_sales") or 0) == 0 and (sw.get("orders") or 0) == 0:
        add(HIGH, "🟠", "Swiggy had zero orders",
            f"Swiggy reported no orders on {rep:%d %b}. Check listing status / availability.")
    # Rating below threshold (only if a live rating is configured)
    thr = settings.RATING_ALERT_THRESHOLD
    for name, val in (("Google", settings.GOOGLE_RATING), ("Zomato", settings.ZOMATO_RATING),
                      ("Swiggy", settings.SWIGGY_RATING)):
        if val and 0 < val < thr:
            add(HIGH, "🟠", f"{name} rating {val:.1f} below {thr:.1f}",
                "Review recent feedback and respond to unhappy customers.")

    # Medium — housekeeping
    if zero_count >= 5:
        add(MEDIUM, "🔵", f"{zero_count} items with zero sales in 14 days",
            "Consider trimming or re-promoting them.", "/results", "See menu")

    items.sort(key=lambda x: x["pri"])
    for it in items:
        it["pri_label"] = _PRI_LABEL[it["pri"]]
    return items[:5]  # never more than five


# ── 5. Wins of the Day (1-2 positives) ───────────────────────────────────────
def _wins(summary, target, trend, top) -> list[dict]:
    cands: list[dict] = []
    rep = summary["data_through"]

    # Best day this week
    if trend and trend.get("high") and trend["high"]["date"] == rep and (trend["high"]["net"] or 0) > 0:
        cands.append({"icon": "🏆", "title": "Best sales day this week",
                      "detail": f"₹{summary['latest_day_sales']:,.0f} on {rep:%a %d %b} — your highest in 7 days."})
    # Sales grew like-for-like
    pw = summary.get("pct_vs_same_weekday")
    if pw is not None and pw > 0:
        cands.append({"icon": "📈", "title": f"Sales up {float(pw):.0f}% vs last {rep:%A}",
                      "detail": "Like-for-like growth against the same weekday last week."})
    # Growth streak
    if trend and trend.get("streak_dir") == "up" and trend.get("streak_len", 0) >= 2:
        cands.append({"icon": "🔥", "title": f"{trend['streak_len']} consecutive growth days",
                      "detail": "Sales have risen every day this stretch."})
    # Data trusted
    if summary.get("data_status") == "DATA TRUSTED":
        cands.append({"icon": "✅", "title": "Data trusted",
                      "detail": "Every channel reconciled — you can act on these numbers with confidence."})
    # Top seller
    if top:
        t0 = top[0]
        cands.append({"icon": "⭐", "title": f"Top seller: {t0['item']}",
                      "detail": f"₹{t0['revenue']:,.0f} — leading the menu."})
    return cands[:2]


# ── 10. GM Actions (max 3, priority / owner / deadline) ──────────────────────
def _gm_actions(db: Session, rep: datetime.date) -> list[dict]:
    raw = get_actions(db)[:3]
    # Suggested deadline: the coming Friday relative to the reporting date.
    wd = rep.weekday()
    ahead = (4 - wd) % 7 or 7
    deadline = rep + datetime.timedelta(days=ahead)
    out = []
    for a in raw:
        pri = a.get("priority") or 3
        label = "HIGH" if pri <= 1 else ("MEDIUM" if pri == 2 else "LOW")
        out.append(
            {
                "priority": label,
                "action": a["action"],
                "owner": "Restaurant Manager",
                "deadline": deadline,
                "deadline_label": deadline.strftime("%A"),
            }
        )
    return out


# ── orchestrator ─────────────────────────────────────────────────────────────
# Freshness-keyed cache. The brief only changes when a new file is uploaded, so
# a single cheap max(uploaded_at) probe tells us whether the cached context is
# still valid. Repeat loads (refresh / navigation) then cost one round-trip
# instead of ~10 — the page stays well under the load budget between uploads,
# and invalidates the instant new data lands. Cached dict is never mutated by
# callers (the route merges user/request into a fresh dict), so sharing is safe.
_CACHE: dict = {"key": None, "ctx": None}


def build_brief(db: Session, *, use_cache: bool = True) -> dict:
    last_refresh = _last_refresh(db)  # doubles as the cache freshness key
    if use_cache and _CACHE["ctx"] is not None and _CACHE["key"] == last_refresh:
        return _CACHE["ctx"]

    ctx = _compute_brief(db, last_refresh)
    _CACHE["key"], _CACHE["ctx"] = last_refresh, ctx
    return ctx


def _compute_brief(db: Session, last_refresh) -> dict:
    summary = get_summary(db)
    if not summary:
        # Error handling: no data at all -> Data Pending, never fake values.
        return {"has_data": False, "data_status": "DATA PENDING", "kpi_meta": kpi_registry()}

    rep = summary["data_through"]

    # One sales read feeds target, trend AND contribution (no duplicate queries).
    series = _sales_series(db, rep)
    month_start = rep.replace(day=1)
    mtd = sum((v for d, v in series.items() if d >= month_start), D(0))
    target = _target(rep, mtd)                                             # pure
    trend = _trend(series, rep)                                            # pure
    contrib = _contribution(rep, summary["latest_day_sales"] or D(0),      # pure
                            mtd, summary["pct_vs_prior_day"])

    # Remaining independent reads (one round-trip each).
    data_trust = get_data_trust(db)
    channel_status = get_channel_upload_status(db)
    failed = _failed_rows_by_channel(db)
    rows = _reporting_date_rows(db, rep)
    menu_rows = get_menu(db)
    customer = _customer(db)
    gm_actions = _gm_actions(db, rep)

    top = sorted((r for r in menu_rows if r["bucket"] == "top"), key=lambda r: r["revenue"], reverse=True)
    bottom = sorted((r for r in menu_rows if r["bucket"] == "bottom"), key=lambda r: r["revenue"])
    zero = sorted((r for r in menu_rows if r["bucket"] == "zero"), key=lambda r: r["item"])
    data_status = summary["data_status"]

    return {
        "has_data": True,
        # existing keys (backward compatible)
        "summary": summary,
        "trusted": data_status == "DATA TRUSTED",
        "top": top,
        "bottom": bottom,
        "zero": zero,
        "menu_as_of": menu_rows[0]["as_of"] if menu_rows else None,
        "channel_status": channel_status,
        "failed_rows_by_channel": failed,
        "target": target,
        "trend": trend,
        # v3 sections
        "exec": _executive_summary(summary, target, last_refresh, data_status),
        "reporting_date": rep,
        "last_refresh": last_refresh,
        "data_status": data_status,
        "contrib": contrib,
        "attention": _attention(summary, target, data_trust, rows, channel_status, failed, len(zero)),
        "wins": _wins(summary, target, trend, top),
        "channels": _channel_performance(summary, rows),
        "customer": customer,
        "operations": _operations(channel_status, failed, data_trust),
        "gm_actions": gm_actions,
        # requirement 11 — hidden governance payload (not user-visible)
        "kpi_meta": kpi_registry(),
    }
