"""Business Target Engine — computes realistic monthly targets from the
canonical Business Settings, never an arbitrary number.

Three targets (the restaurant's financial philosophy):
  * Break-even  = fixed expenses / contribution margin
  * Operating   = (fixed expenses + desired profit) / contribution margin
  * Stretch     = operating x (1 + growth%)

Plus a month-end projection (from the current daily average) compared against
each target, a data-confidence grade from trusted completed months, a *suggested*
growth % (never auto-applied), and a full explainability payload per target so
the owner can always answer "How was this calculated?".

Inputs come exclusively from business_settings (the one source of truth) and
trusted sales history — so every dashboard/KPI/forecast shares one model.
"""
import calendar
import datetime
import decimal
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import business_settings as bs

D = decimal.Decimal
_CENT = D("0.01")
_OWNER = "Owner"


def _money(x: decimal.Decimal) -> decimal.Decimal:
    return D(str(x)).quantize(_CENT)


def _trusted_completed_months(db: Session, today: datetime.date) -> list[dict]:
    """Completed calendar months (before the current one) that hold sales data
    and have NO unexplained reconciliation mismatch — newest first. Each row:
    {month, total, days, daily_avg}. Partial/untrusted months are excluded."""
    rows = db.execute(
        text(
            """
            WITH months AS (
                SELECT date_trunc('month', business_date)::date AS m,
                       sum(net_sales) AS total,
                       count(DISTINCT business_date) AS days
                FROM daily_channel_sales
                WHERE business_date < date_trunc('month', :today)::date
                GROUP BY 1
            ), bad AS (
                SELECT DISTINCT date_trunc('month', business_date)::date AS m
                FROM v_recon_daily
                WHERE status = 'MISMATCH'
                  AND business_date < date_trunc('month', :today)::date
            )
            SELECT m.m, m.total, m.days
            FROM months m
            WHERE m.m NOT IN (SELECT m FROM bad) AND m.days > 0
            ORDER BY m.m DESC
            """
        ),
        {"today": today},
    ).mappings().all()
    out = []
    for r in rows:
        days = r["days"] or 0
        total = D(str(r["total"] or 0))
        out.append({
            "month": r["m"],
            "total": _money(total),
            "days": days,
            "daily_avg": _money(total / days) if days else D(0),
        })
    return out


def _confidence(n_trusted_months: int) -> tuple[str, str]:
    if n_trusted_months >= 3:
        return "High", "based on 3+ trusted completed months"
    if n_trusted_months == 2:
        return "Medium", "based on 2 trusted completed months"
    if n_trusted_months == 1:
        return "Low", "based on 1 trusted completed month"
    return "Very Low", "no trusted completed month yet — projection uses the current month only"


def _recommended_growth(history: list[dict]) -> tuple[float | None, str]:
    """Month-over-month growth of the two most recent trusted months' daily
    averages. A *suggestion* only — the owner must approve before it's applied."""
    if len(history) < 2:
        return None, "Needs 2 trusted completed months to recommend a growth %."
    recent, prev = history[0]["daily_avg"], history[1]["daily_avg"]
    if prev <= 0:
        return None, "Previous month had no comparable sales."
    g = float((recent - prev) / prev * 100)
    g = max(0.0, min(g, 50.0))  # clamp to a sane planning range
    return round(g, 1), f"Suggested from {history[1]['month']:%b}->{history[0]['month']:%b} daily-average change (approve to apply)."


def compute(db: Session, *, mtd: decimal.Decimal, reporting_date: datetime.date,
            days_elapsed: int, calculated_at: datetime.datetime | None = None) -> dict:
    """Assemble the full Business Targets payload."""
    calculated_at = calculated_at or datetime.datetime.now()
    fin = bs.get_financials(db)
    # One expense read -> both the monthly total and the "is it configured?" flag.
    expenses = bs.get_active_expenses(db)
    fixed = sum((e.monthly_equivalent for e in expenses), D(0)).quantize(_CENT)
    desired_profit = _money(fin["desired_profit"])
    margin_pct = float(fin["margin_pct"])
    growth_pct = float(fin["growth_pct"])
    margin = D(str(fin["margin_pct"])) / 100

    configured = len(expenses) > 0
    computable = margin > 0

    break_even = operating = stretch = None
    if computable:
        break_even = _money(fixed / margin)
        operating = _money((fixed + desired_profit) / margin)
        stretch = _money(operating * (1 + D(str(growth_pct)) / 100))

    # ── projection from the current month's daily average ──
    days_in_month = calendar.monthrange(reporting_date.year, reporting_date.month)[1]
    remaining = days_in_month - days_elapsed
    daily_avg = _money(mtd / days_elapsed) if days_elapsed else D(0)
    projected = _money(mtd + daily_avg * remaining)

    # ── historical intelligence / confidence / suggested growth ──
    history = _trusted_completed_months(db, reporting_date)
    confidence, confidence_note = _confidence(len(history))
    rec_growth, rec_note = _recommended_growth(history)

    def _vs(target):
        if target is None or target == 0:
            return None
        return {
            "value": target,
            "on_track": projected >= target,
            "gap": _money(projected - target),
            "pct": round(float(projected / target * 100), 0),
        }

    vs = {"break_even": _vs(break_even), "operating": _vs(operating), "stretch": _vs(stretch)}
    primary = vs["operating"]
    primary_status = "on_track" if (primary and primary["on_track"]) else "below"

    # ── explainability (the "How was this calculated?" payloads) ──
    fx = f"₹{fixed:,.0f}"
    mg = f"{margin_pct:.0f}%"
    pf = f"₹{desired_profit:,.0f}"
    explain = {
        "break_even": {
            "formula": "Fixed monthly expenses ÷ contribution margin",
            "inputs": {"Fixed expenses": fx, "Contribution margin": mg},
            "data_source": "Business Settings → fixed_expenses, contribution_margin_pct",
            "confidence": "High (direct from configured inputs)",
            "owner": _OWNER,
        },
        "operating": {
            "formula": "(Fixed monthly expenses + desired profit) ÷ contribution margin",
            "inputs": {"Fixed expenses": fx, "Desired profit": pf, "Contribution margin": mg},
            "data_source": "Business Settings → fixed_expenses, desired_monthly_profit, contribution_margin_pct",
            "confidence": "High (direct from configured inputs)",
            "owner": _OWNER,
        },
        "stretch": {
            "formula": "Operating target × (1 + growth %)",
            "inputs": {"Operating target": f"₹{operating:,.0f}" if operating else "—", "Growth %": f"{growth_pct:.0f}%"},
            "data_source": "Business Settings → growth_pct",
            "confidence": "High (direct from configured inputs)",
            "owner": _OWNER,
        },
        "projection": {
            "formula": "MTD sales + (current daily average × remaining days)",
            "inputs": {
                "MTD sales": f"₹{mtd:,.0f}",
                "Daily average": f"₹{daily_avg:,.0f}",
                "Days elapsed": str(days_elapsed),
                "Remaining days": str(remaining),
            },
            "data_source": "daily_channel_sales (trusted) + calendar",
            "confidence": f"{confidence} — {confidence_note}",
            "owner": _OWNER,
        },
    }

    return {
        "configured": configured,
        "computable": computable,
        "fixed_expenses": fixed,
        "desired_profit": desired_profit,
        "margin_pct": margin_pct,
        "growth_pct": growth_pct,
        "break_even": break_even,
        "operating": operating,
        "stretch": stretch,
        "mtd": _money(mtd),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "remaining_days": remaining,
        "daily_avg": daily_avg,
        "projected_month_end": projected,
        "confidence": confidence,
        "confidence_note": confidence_note,
        "recommended_growth_pct": rec_growth,
        "recommended_note": rec_note,
        "history": history,
        "vs": vs,
        "primary_status": primary_status,
        "explain": explain,
        "calculated_at": calculated_at,
        "data_as_of": reporting_date,
    }
