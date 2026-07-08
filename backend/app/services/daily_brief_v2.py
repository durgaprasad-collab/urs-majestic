"""Daily Brief v2 metrics: sales target vs achievement, an estimated
contribution figure, a last-7-day sales trend, and an Attention Required rule
engine.

Everything here is derived from already-trusted data — daily_channel_sales, the
recon/KPI views, upload_log and v_ceo_brief_summary. Contribution is an ESTIMATE
using a single assumed food-cost % (see settings.ASSUMED_FOOD_COST_PCT); swap
_contrib_after_food_cost() for a per-item cost lookup once those inputs exist.
"""
import calendar
import datetime
import decimal
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

D = decimal.Decimal


def _data_through(db: Session) -> datetime.date | None:
    return db.execute(text("SELECT max(business_date) FROM daily_channel_sales")).scalar()


def _sum_net(db: Session, start: datetime.date, end: datetime.date) -> decimal.Decimal:
    v = db.execute(
        text(
            "SELECT COALESCE(sum(net_sales), 0) FROM daily_channel_sales "
            "WHERE business_date >= :s AND business_date <= :e"
        ),
        {"s": start, "e": end},
    ).scalar()
    return D(str(v or 0))


# ── 🎯 Sales Target vs Achievement ──────────────────────────────────────────
def get_target_vs_achievement(db: Session) -> dict | None:
    """Month-to-date net sales vs the configured monthly target, with pace
    (where you *should* be by now) and a straight-line projection. Returns None
    if there's no sales data; target_set=False when no target is configured."""
    through = _data_through(db)
    if not through:
        return None

    target = D(str(settings.MONTHLY_SALES_TARGET or 0))
    month_start = through.replace(day=1)
    days_in_month = calendar.monthrange(through.year, through.month)[1]
    days_elapsed = through.day  # through is the latest day we hold data for
    mtd = _sum_net(db, month_start, through)

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
        out.update(
            {
                "pct_of_target": float(mtd / target * 100),
                "expected_to_date": expected,
                "pace_delta": mtd - expected,  # + ahead of pace / − behind
                "pace_pct": float((mtd - expected) / expected * 100) if expected else 0.0,
                "projection": projection,
                "projection_pct_of_target": float(projection / target * 100),
                "on_track": mtd >= expected,
            }
        )
    return out


# ── 💰 Estimated Contribution ────────────────────────────────────────────────
def _contrib_after_food_cost(sales: decimal.Decimal) -> decimal.Decimal:
    """Sales minus estimated food cost. Flat assumption for now — replace with a
    per-item cost rollup once item-level costs are available for all channels."""
    fc = D(str(settings.ASSUMED_FOOD_COST_PCT or 0))
    return sales * (1 - fc)


def get_estimated_contribution(db: Session) -> dict | None:
    through = _data_through(db)
    if not through:
        return None

    fc_pct = float(D(str(settings.ASSUMED_FOOD_COST_PCT or 0)) * 100)
    day_sales = _sum_net(db, through, through)
    mtd_sales = _sum_net(db, through.replace(day=1), through)
    return {
        "as_of": through,
        "estimated": True,  # flip to False when real per-item costs feed in
        "food_cost_pct": fc_pct,
        "contribution_pct": 100.0 - fc_pct,
        "day_sales": day_sales,
        "day_contribution": _contrib_after_food_cost(day_sales),
        "mtd_sales": mtd_sales,
        "mtd_contribution": _contrib_after_food_cost(mtd_sales),
    }


# ── 📈 Last 7-Day Trend ──────────────────────────────────────────────────────
def get_seven_day_trend(db: Session) -> dict | None:
    """Total net sales for each of the last 7 days up to the latest data date.
    Missing days are filled with 0 so the sparkline reads honestly. Each row
    carries a pct (0–100) height relative to the peak day."""
    through = _data_through(db)
    if not through:
        return None

    start = through - datetime.timedelta(days=6)
    rows = db.execute(
        text(
            "SELECT business_date AS d, sum(net_sales) AS net "
            "FROM daily_channel_sales WHERE business_date >= :s AND business_date <= :e "
            "GROUP BY business_date"
        ),
        {"s": start, "e": through},
    ).mappings().all()
    by_date = {r["d"]: D(str(r["net"] or 0)) for r in rows}

    peak = max(by_date.values(), default=D(0))
    days = []
    for i in range(7):
        d = start + datetime.timedelta(days=i)
        net = by_date.get(d, D(0))
        pct = int(net / peak * 100) if peak > 0 else 0
        days.append({"date": d, "net": net, "has_data": d in by_date, "pct": pct})
    return {"days": days, "peak": peak}


# ── ⚠️ Attention Required (rule engine) ──────────────────────────────────────
# Severity: 1 = high (red), 2 = medium (amber), 3 = low (info). Each rule is
# small and explainable on purpose — per the kill criterion, if a rule isn't
# driving action after two weeks, delete or rewrite just that block.
_SALES_DROP_PCT = D("-20")   # yesterday vs same weekday last week
_PACE_BEHIND_PCT = -10.0     # month-to-date vs expected pace
_ZERO_SELLER_MIN = 5         # count of 14-day zero-sellers worth flagging


def get_attention_items(db: Session) -> list[dict]:
    items: list[dict] = []

    summary = db.execute(text("SELECT * FROM v_ceo_brief_summary")).mappings().first()

    # 1 ── unexplained data mismatches (trust)
    trust = db.execute(text("SELECT * FROM v_data_trust")).mappings().first()
    if trust and (trust["unexplained_mismatches"] or 0) > 0:
        n = trust["unexplained_mismatches"]
        oldest = trust["oldest_mismatch_date"]
        items.append(
            {
                "sev": 1,
                "icon": "🔴",
                "title": f"{n} unexplained data mismatch(es)",
                "detail": f"Oldest since {oldest:%d %b}. Reconcile before trusting today's numbers."
                if oldest
                else "Reconcile before trusting today's numbers.",
                "link": "/data-reconciliation",
                "link_label": "Reconcile",
            }
        )

    # 2 ── stale channels (uploads falling behind)
    stale = db.execute(
        text("SELECT channel, days_behind_freshest FROM v_channel_upload_status WHERE is_stale ORDER BY days_behind_freshest DESC")
    ).mappings().all()
    for s in stale:
        items.append(
            {
                "sev": 2,
                "icon": "🟠",
                "title": f"{s['channel'].title()} data is {s['days_behind_freshest']}d behind",
                "detail": "Upload the latest file so sales and contribution stay current.",
                "link": "/upload",
                "link_label": "Upload",
            }
        )

    # 3 ── rows dropped in the most recent upload of each channel
    failed = db.execute(
        text(
            """
            SELECT DISTINCT ON (channel) channel, rows_failed
            FROM upload_log ORDER BY channel, uploaded_at DESC
            """
        )
    ).mappings().all()
    for f in failed:
        if (f["rows_failed"] or 0) > 0:
            items.append(
                {
                    "sev": 2,
                    "icon": "🟠",
                    "title": f"{f['channel'].title()}: {f['rows_failed']} row(s) failed to import",
                    "detail": "Some sales are missing from the totals below.",
                    "link": "/data-reconciliation",
                    "link_label": "Review",
                }
            )

    # 4 ── yesterday's sales well below the same weekday last week
    if summary and summary["pct_vs_same_weekday"] is not None:
        pct = D(str(summary["pct_vs_same_weekday"]))
        if pct <= _SALES_DROP_PCT:
            items.append(
                {
                    "sev": 2,
                    "icon": "🟠",
                    "title": f"Sales down {abs(float(pct)):.0f}% vs same weekday",
                    "detail": f"₹{summary['latest_day_sales']:,.0f} on {summary['data_through']:%a %d %b}. Check staffing/menu for that day.",
                    "link": None,
                    "link_label": None,
                }
            )

    # 5 ── month-to-date behind the target pace
    tgt = get_target_vs_achievement(db)
    if tgt and tgt.get("target_set") and tgt.get("pace_pct", 0) <= _PACE_BEHIND_PCT:
        items.append(
            {
                "sev": 2,
                "icon": "🟠",
                "title": f"Behind monthly pace by {abs(tgt['pace_pct']):.0f}%",
                "detail": f"₹{tgt['mtd']:,.0f} vs ₹{tgt['expected_to_date']:,.0f} expected by now. Projected ₹{tgt['projection']:,.0f} ({tgt['projection_pct_of_target']:.0f}% of target).",
                "link": None,
                "link_label": None,
            }
        )

    # 6 ── dead menu items (no sales in 14 days)
    zero_count = db.execute(
        text("SELECT count(*) FROM v_ceo_brief_menu WHERE bucket = 'zero'")
    ).scalar() or 0
    if zero_count >= _ZERO_SELLER_MIN:
        items.append(
            {
                "sev": 3,
                "icon": "🔵",
                "title": f"{zero_count} items with zero sales in 14 days",
                "detail": "Consider trimming or re-promoting them.",
                "link": "/results",
                "link_label": "See menu",
            }
        )

    items.sort(key=lambda x: x["sev"])
    return items
