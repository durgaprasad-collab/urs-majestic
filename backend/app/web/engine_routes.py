"""Cost engine trigger and reconciliation page."""
import calendar
import decimal
from collections import defaultdict
from datetime import date
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import settings
from app.models.purchase import Purchase
from app.models.ingredient import Ingredient, IngredientDishMap
from app.models.menu_item import MenuItem
from app.models.item_sale import ItemSale
from app.services import business_settings as bs
from app.services import target_engine
from app.services.menu_engineering.cost_engine import run_cost_engine
from app.web.deps import _tmpl, require_user
from app.core.clock import business_today

D = decimal.Decimal
_INTENSITY_PORTION_COL = {
    "light": "portion_light_g",
    "medium": "portion_medium_g",
    "heavy": "portion_heavy_g",
}

router = APIRouter(tags=["engine"])


def _purchase_qty_in_ingredient_unit(purchase: Purchase, ingredient: Ingredient) -> float:
    """Normalize mixed purchase units before reconciliation totals."""
    qty = float(purchase.qty)
    source = str(purchase.unit)
    target = str(ingredient.unit)
    if source == target:
        return qty
    conversions = {
        ("kg", "g"): 1000.0, ("g", "kg"): 0.001,
        ("l", "ml"): 1000.0, ("ml", "l"): 0.001,
    }
    if (source, target) in conversions:
        return qty * conversions[(source, target)]
    pack_size = float(ingredient.pack_size_g) if ingredient.pack_size_g else None
    if target == "pcs" and pack_size:
        if source == "kg":
            return qty * 1000.0 / pack_size
        if source == "g":
            return qty / pack_size
    return qty


def _parse_month_key(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 7:
            return date.fromisoformat(f"{raw}-01")
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _month_options(db: Session) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT month_start
              FROM (
                    SELECT DISTINCT date_trunc('month', business_date)::date AS month_start
                      FROM daily_channel_sales
                    UNION
                    SELECT DISTINCT date_trunc('month', purchase_date)::date AS month_start
                      FROM purchases
                     WHERE deleted_at IS NULL
              ) months
             ORDER BY month_start DESC
        """)
    ).scalars().all()
    return [{"key": row.strftime("%Y-%m"), "label": row.strftime("%b %Y"), "start": row} for row in rows]


def _month_bounds(month_start: date) -> tuple[date, date]:
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    return month_start, date(month_start.year, month_start.month, last_day)


def _monthly_target_total(db: Session, report_date: date) -> tuple[decimal.Decimal, str]:
    computed = target_engine.compute(
        db,
        mtd=D("0"),
        reporting_date=report_date,
        days_elapsed=max(report_date.day, 1),
    )
    break_even = D(str(computed.get("break_even") or 0))
    if break_even > 0:
        return break_even.quantize(D("0.01")), "Break-even target"
    configured = D(str(settings.MONTHLY_SALES_TARGET or 0))
    if configured > 0:
        return configured.quantize(D("0.01")), "Configured monthly sales target"
    return D("0.00"), "No target configured"


def _calendar_month_ledger(db: Session, month_start: date, month_end: date, display_end: date) -> dict:
    rows = db.execute(
        text("""
            WITH days AS (
                SELECT generate_series(CAST(:month_start AS date), CAST(:display_end AS date), interval '1 day')::date AS business_date
            ),
            revenue AS (
                SELECT business_date, COALESCE(SUM(net_sales), 0) AS revenue
                  FROM daily_channel_sales
                 WHERE business_date >= :month_start
                   AND business_date <= :display_end
                 GROUP BY business_date
            ),
            spend AS (
                SELECT purchase_date AS business_date, COALESCE(SUM(total_price), 0) AS purchases
                  FROM purchases
                 WHERE deleted_at IS NULL
                   AND purchase_date >= :month_start
                   AND purchase_date <= :display_end
                 GROUP BY purchase_date
            )
            SELECT d.business_date,
                   COALESCE(r.revenue, 0) AS revenue,
                   COALESCE(s.purchases, 0) AS purchases
              FROM days d
         LEFT JOIN revenue r ON r.business_date = d.business_date
         LEFT JOIN spend s ON s.business_date = d.business_date
             ORDER BY d.business_date
        """),
        {"month_start": month_start, "display_end": display_end},
    ).mappings().all()

    target_total, target_source = _monthly_target_total(db, display_end)
    fixed_total = bs.monthly_fixed_total(db, display_end)
    days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
    revenue_total = D("0.00")
    purchases_total = D("0.00")
    ledger_rows: list[dict] = []
    for idx, row in enumerate(rows, start=1):
        revenue = D(str(row["revenue"] or 0)).quantize(D("0.01"))
        purchases = D(str(row["purchases"] or 0)).quantize(D("0.01"))
        revenue_total += revenue
        purchases_total += purchases
        target_to_date = (target_total * D(idx) / D(days_in_month)).quantize(D("0.01")) if target_total else None
        achieved_to_date = revenue_total.quantize(D("0.01"))
        achieved_pct = (achieved_to_date / target_to_date * 100) if target_to_date else None
        ledger_rows.append({
            "business_date": row["business_date"],
            "revenue": revenue,
            "purchases": purchases,
            "target_to_date": target_to_date,
            "achieved_to_date": achieved_to_date,
            "achieved_pct": round(float(achieved_pct), 1) if achieved_pct is not None else None,
        })

    month_subtotal_label = "Subtotal to date" if display_end < month_end else "Month subtotal"
    target_subtotal = target_total if target_total else None
    cash_needed_total = (fixed_total + purchases_total).quantize(D("0.01"))
    cash_gap_total = (cash_needed_total - revenue_total).quantize(D("0.01"))
    achieved_subtotal = revenue_total if target_total else None
    achieved_subtotal_pct = (revenue_total / target_total * 100) if target_total else None
    return {
        "rows": ledger_rows,
        "revenue_total": revenue_total.quantize(D("0.01")),
        "purchases_total": purchases_total.quantize(D("0.01")),
        "fixed_total": fixed_total.quantize(D("0.01")),
        "cash_needed_total": cash_needed_total,
        "cash_gap_total": cash_gap_total,
        "cash_gap_abs": abs(cash_gap_total),
        "cash_gap_short": cash_gap_total > 0,
        "target_total": target_subtotal.quantize(D("0.01")) if target_subtotal is not None else None,
        "achieved_total": achieved_subtotal.quantize(D("0.01")) if achieved_subtotal is not None else revenue_total.quantize(D("0.01")),
        "achieved_total_pct": round(float(achieved_subtotal_pct), 1) if achieved_subtotal_pct is not None else None,
        "target_source": target_source,
        "month_subtotal_label": month_subtotal_label,
        "days_in_month": days_in_month,
    }


@router.post("/run-cost-engine")
async def run_engine(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    run_cost_engine(db)
    db.commit()
    return RedirectResponse("/results?notice=Food+costs+recalculated.", status_code=303)


@router.get("/reconciliation", response_class=HTMLResponse)
def reconciliation(request: Request, db: Session = Depends(get_db)):
    """Reconcile: money in vs money out, month by month, plus the channel
    data check (services/reconcile_page.py). Replaced the long purchase-cost
    page on 2026-10-09; per-item purchase detail now lives on Purchases."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    from app.services import reconcile_page
    return _tmpl(request, "reconciliation.html", {
        "user": user, **reconcile_page.page(db, request.query_params.get("month")),
        "notice": request.query_params.get("notice"),
    })
