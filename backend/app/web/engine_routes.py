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
    configured = D(str(settings.MONTHLY_SALES_TARGET or 0))
    if configured > 0:
        return configured.quantize(D("0.01")), "Configured monthly sales target"

    computed = target_engine.compute(
        db,
        mtd=D("0"),
        reporting_date=report_date,
        days_elapsed=max(report_date.day, 1),
    )
    operating = D(str(computed.get("operating") or 0))
    if operating > 0:
        return operating.quantize(D("0.01")), "Operating target"
    break_even = D(str(computed.get("break_even") or 0))
    if break_even > 0:
        return break_even.quantize(D("0.01")), "Break-even target"
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
    achieved_subtotal = revenue_total if target_total else None
    achieved_subtotal_pct = (revenue_total / target_total * 100) if target_total else None
    return {
        "rows": ledger_rows,
        "revenue_total": revenue_total.quantize(D("0.01")),
        "purchases_total": purchases_total.quantize(D("0.01")),
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
    return RedirectResponse("/results?engine=1", status_code=303)


@router.get("/reconciliation", response_class=HTMLResponse)
def reconciliation(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    # Optional ingredient filter. Scopes the cost cards, the purchase summary and
    # the usage-by-dish table to a single ingredient. Unmapped-ingredient warnings
    # are deliberately NOT scoped — that panel is a global data-hygiene alert and
    # hiding it behind a filter would let unmapped cost go unnoticed.
    raw_ing = request.query_params.get("ingredient_id", "")
    filter_id = int(raw_ing) if raw_ing.isdigit() else None
    filter_ing = db.get(Ingredient, filter_id) if filter_id else None
    if filter_ing is None:
        filter_id = None

    today = business_today()
    month_options = _month_options(db)
    raw_month = request.query_params.get("month", "")
    requested_month = _parse_month_key(raw_month)
    selected_month = requested_month or (month_options[0]["start"] if month_options else today.replace(day=1))
    selected_month_start, selected_month_end = _month_bounds(selected_month)
    selected_month_display_end = (
        min(selected_month_end, today)
        if (selected_month_start.year == today.year and selected_month_start.month == today.month)
        else selected_month_end
    )
    selected_month_key = selected_month_start.strftime("%Y-%m")
    selected_month_label = selected_month_start.strftime("%B %Y")
    month_ledger = _calendar_month_ledger(db, selected_month_start, selected_month_end, selected_month_display_end)

    current_month_index = today.year * 12 + today.month - 1
    month_starts = [
        date((current_month_index - offset) // 12, (current_month_index - offset) % 12 + 1, 1)
        for offset in (2, 1, 0)
    ]
    month_keys = [m.strftime("%Y-%m") for m in month_starts]
    months = [{"key": key, "label": start.strftime("%b %Y")} for key, start in zip(month_keys, month_starts)]
    window_start = month_starts[0]

    # All ingredients that have at least one purchase — the only ones that can
    # produce rows below, so the dropdown never offers a choice that yields nothing.
    purchased_ids = {
        r[0]
        for r in db.query(Purchase.ingredient_id)
        .filter(Purchase.deleted_at.is_(None))
        .distinct()
        .all()
    }
    filter_options = (
        db.query(Ingredient)
        .filter(Ingredient.id.in_(purchased_ids))
        .order_by(Ingredient.name)
        .all()
        if purchased_ids else []
    )

    # Total cost by usage type
    agg_q = db.query(Purchase.usage_type, func.sum(Purchase.total_price).label("total")).filter(
        Purchase.deleted_at.is_(None),
        Purchase.purchase_date >= window_start,
        Purchase.purchase_date <= today,
    )
    if filter_id:
        agg_q = agg_q.filter(Purchase.ingredient_id == filter_id)
    agg = agg_q.group_by(Purchase.usage_type).all()
    totals = {row.usage_type: float(row.total) for row in agg}
    total_menu_cost = totals.get("menu", 0.0)
    total_personal_cost = totals.get("others_personal", 0.0)
    total_excluded_cost = totals.get("excluded_unidentified", 0.0)
    grand_total = sum(totals.values())

    # Unmapped ingredients that have menu-usage purchases
    mapped_ingredient_ids = {
        row[0]
        for row in db.query(IngredientDishMap.ingredient_id).distinct().all()
    }
    menu_ingredient_ids = {
        row[0]
        for row in db.query(Purchase.ingredient_id)
        .filter(
            Purchase.usage_type == "menu",
            Purchase.deleted_at.is_(None),
            Purchase.purchase_date >= window_start,
            Purchase.purchase_date <= today,
        )
        .distinct()
        .all()
    }
    unmapped_ids = menu_ingredient_ids - mapped_ingredient_ids

    unmapped_cost = 0.0
    unmapped_names: list[str] = []
    if unmapped_ids:
        unmapped_agg = (
            db.query(Purchase.ingredient_id, func.sum(Purchase.total_price).label("total"))
            .filter(
                Purchase.ingredient_id.in_(unmapped_ids),
                Purchase.usage_type == "menu",
                Purchase.deleted_at.is_(None),
                Purchase.purchase_date >= window_start,
                Purchase.purchase_date <= today,
            )
            .group_by(Purchase.ingredient_id)
            .all()
        )
        ing_map = {
            i.id: i.name
            for i in db.query(Ingredient).filter(Ingredient.id.in_(unmapped_ids)).all()
        }
        for row in unmapped_agg:
            unmapped_cost += float(row.total)
            unmapped_names.append(ing_map.get(row.ingredient_id, f"ID {row.ingredient_id}"))

    # Per-ingredient purchase summary (restocking interval + avg qty)
    purch_q = (
        db.query(Purchase)
        .filter(
            Purchase.deleted_at.is_(None),
            Purchase.purchase_date >= window_start,
            Purchase.purchase_date <= today,
        )
        .order_by(Purchase.ingredient_id, Purchase.purchase_date)
    )
    if filter_id:
        purch_q = purch_q.filter(Purchase.ingredient_id == filter_id)
    all_purchases = purch_q.all()

    # Three calendar months, category and ingredient/item level. Quantity and
    # spend stay together at item level; category totals are monetary because
    # kilograms, litres, grams and pieces cannot be meaningfully summed.
    all_ingredient_ids = {p.ingredient_id for p in all_purchases}
    rolling_ingredients = {
        i.id: i for i in db.query(Ingredient).filter(Ingredient.id.in_(all_ingredient_ids)).all()
    } if all_ingredient_ids else {}
    month_totals = {key: 0.0 for key in month_keys}
    category_acc = defaultdict(lambda: {key: 0.0 for key in month_keys})
    item_acc = defaultdict(lambda: {
        "months": {key: {"qty": 0.0, "spend": 0.0} for key in month_keys},
        "total_qty": 0.0, "total_spend": 0.0, "unit": None,
    })
    for purchase in all_purchases:
        key = purchase.purchase_date.strftime("%Y-%m")
        if key not in month_totals:
            continue
        ingredient = rolling_ingredients.get(purchase.ingredient_id)
        category = (ingredient.category if ingredient else None) or "Other"
        amount = float(purchase.total_price)
        qty = _purchase_qty_in_ingredient_unit(purchase, ingredient) if ingredient else float(purchase.qty)
        month_totals[key] += amount
        category_acc[category][key] += amount
        item_key = (purchase.ingredient_id, purchase.usage_type)
        row = item_acc[item_key]
        row["name"] = ingredient.name if ingredient else f"ID {purchase.ingredient_id}"
        row["category"] = category
        row["usage_type"] = purchase.usage_type
        row["unit"] = ingredient.unit if ingredient else purchase.unit
        row["months"][key]["qty"] += qty
        row["months"][key]["spend"] += amount
        row["total_qty"] += qty
        row["total_spend"] += amount
    category_monthly = [
        {"category": category, "months": values, "total": sum(values.values())}
        for category, values in category_acc.items()
    ]
    category_monthly.sort(key=lambda row: (-row["total"], row["category"]))
    item_monthly = list(item_acc.values())
    item_monthly.sort(key=lambda row: (-row["total_spend"], row["name"], row["usage_type"]))
    ing_groups: dict[int, list] = defaultdict(list)
    for p in all_purchases:
        ing_groups[p.ingredient_id].append(p)

    ings_by_id = {
        i.id: i
        for i in db.query(Ingredient).filter(Ingredient.id.in_(set(ing_groups.keys()))).all()
    } if ing_groups else {}

    ingredient_summary: list[dict] = []
    for ing_id, purchases in ing_groups.items():
        ing = ings_by_id.get(ing_id)
        if not ing:
            continue
        dates = sorted(p.purchase_date for p in purchases)
        total_qty = sum(_purchase_qty_in_ingredient_unit(p, ing) for p in purchases)
        avg_qty = total_qty / len(purchases)
        total_spent = sum(float(p.total_price) for p in purchases)
        if len(dates) >= 2:
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_days: float | None = sum(gaps) / len(gaps)
        else:
            avg_days = None
        ingredient_summary.append({
            "name": ing.name,
            "count": len(purchases),
            "total_qty": total_qty,
            "avg_qty": avg_qty,
            "unit": ing.unit,
            "avg_days": avg_days,
            "total_spent": total_spent,
            "last_date": max(dates),
        })
    ingredient_summary.sort(key=lambda x: x["name"])

    # Ingredient usage % per dish — share of each ingredient's actual
    # consumption (portion size x units sold) going to each mapped dish.
    sales_agg = (
        db.query(ItemSale.item_name, func.sum(ItemSale.qty).label("units"))
        .filter(ItemSale.sale_date >= window_start, ItemSale.sale_date <= today)
        .group_by(ItemSale.item_name)
        .all()
    )
    units_sold_by_name = {row.item_name: float(row.units) for row in sales_agg}

    maps_q = (
        db.query(IngredientDishMap, Ingredient, MenuItem)
        .join(Ingredient, Ingredient.id == IngredientDishMap.ingredient_id)
        .join(MenuItem, MenuItem.id == IngredientDishMap.menu_item_id)
        .filter(Ingredient.cost_role == "recipe")
    )
    if filter_id:
        maps_q = maps_q.filter(IngredientDishMap.ingredient_id == filter_id)
    maps = maps_q.all()

    usage_groups: dict[str, list[dict]] = defaultdict(list)
    for m, ing, item in maps:
        portion = getattr(ing, _INTENSITY_PORTION_COL.get(m.intensity, "portion_medium_g"))
        units_sold = units_sold_by_name.get(item.name, 0.0)
        grams_used = float(portion) * units_sold if portion is not None else None
        usage_groups[ing.name].append({
            "dish": item.name,
            "intensity": m.intensity,
            "units_sold": units_sold,
            "grams_used": grams_used,
        })

    ingredient_usage: list[dict] = []
    for ing_name, rows in usage_groups.items():
        total_grams = sum(r["grams_used"] for r in rows if r["grams_used"] is not None)
        for r in rows:
            if r["grams_used"] is not None and total_grams > 0:
                r["pct"] = r["grams_used"] / total_grams * 100
            else:
                r["pct"] = None
        rows.sort(key=lambda r: (r["pct"] is None, -(r["pct"] or 0)))
        ingredient_usage.append({"name": ing_name, "rows": rows})
    ingredient_usage.sort(key=lambda x: x["name"])

    # When a filter is on but the ingredient has no recipe mapping, the usage
    # table would silently vanish. Say why instead.
    usage_empty_reason = None
    if filter_ing and not ingredient_usage:
        if filter_ing.cost_role != "recipe":
            usage_empty_reason = (
                f"{filter_ing.name} is not a recipe-role ingredient "
                f"(cost_role = {filter_ing.cost_role}), so it has no per-dish usage split."
            )
        else:
            usage_empty_reason = (
                f"{filter_ing.name} is not mapped to any dish yet."
            )

    return _tmpl(request, "reconciliation.html", {
        "user": user,
        "total_menu_cost": total_menu_cost,
        "total_personal_cost": total_personal_cost,
        "total_excluded_cost": total_excluded_cost,
        "grand_total": grand_total,
        "unmapped_cost": unmapped_cost,
        "unmapped_names": sorted(unmapped_names),
        "ingredient_summary": ingredient_summary,
        "ingredient_usage": ingredient_usage,
        "filter_options": filter_options,
        "filter_id": filter_id,
        "filter_ing": filter_ing,
        "usage_empty_reason": usage_empty_reason,
        "month_options": month_options,
        "selected_month_key": selected_month_key,
        "selected_month_label": selected_month_label,
        "selected_month_display_end": selected_month_display_end,
        "month_ledger": month_ledger,
        "months": months,
        "month_totals": month_totals,
        "category_monthly": category_monthly,
        "item_monthly": item_monthly,
        "window_start": window_start,
        "window_end": today,
    })
