"""Seven-day ingredient demand forecasting for weekly inventory orders.

Demand is derived from actual item sales and the existing recipe inputs.  Each
ingredient is backtested against simple seasonal/trend baselines and two small
machine-learning models.  Complexity only wins when its held-out error is
lower, which matters more than calling every forecast "AI".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
import math
import statistics

from sqlalchemy import text

from app.core.clock import business_today
from app.models.item_sale import ItemSale
from app.services.sales_stock import calculate_ingredient_usage


MODEL_VERSION = "weekly-v1"
CONSOLIDATED_WEEKLY_CATEGORY = "All categories"
DEFAULT_WEEKLY_CATEGORY = CONSOLIDATED_WEEKLY_CATEGORY


_CATEGORIES_SQL = text("""
    SELECT COALESCE(i.category, 'Other') AS category,
           COUNT(*) AS total_ingredients,
           COUNT(*) FILTER (WHERE m.ingredient_id IS NOT NULL) AS mapped_ingredients
      FROM ingredients i
      LEFT JOIN ingredient_dish_map m ON m.ingredient_id = i.id
     WHERE i.is_active
     GROUP BY COALESCE(i.category, 'Other')
     ORDER BY mapped_ingredients DESC, total_ingredients DESC, category
""")


def weekly_forecast_categories(db) -> list[str]:
    """Return forecastable categories in a stable, useful order."""
    rows = db.execute(_CATEGORIES_SQL).mappings().all()
    return [r["category"] for r in rows if int(r["mapped_ingredients"] or 0) > 0]


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _shift_months(month_start: date, months: int) -> date:
    month_index = month_start.year * 12 + month_start.month - 1 + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def _cadence_forecast(records: list[tuple[date, float]], future: list[date]) -> dict | None:
    """Fallback forecast from purchase cadence when no recipe mapping exists."""
    records = [(d, q) for d, q in records if q is not None and q > 0]
    if len(records) < 2:
        return None
    records.sort(key=lambda row: row[0])
    dates = [d for d, _ in records]
    qtys = [q for _, q in records]
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    avg_days = _mean(gaps) if gaps else None
    avg_qty = _mean(qtys)
    if avg_qty <= 0:
        return None
    cadence_days = max(avg_days or 0.0, 1.0)
    daily = avg_qty / cadence_days
    total = daily * len(future)
    gap_spread = statistics.pstdev(gaps) / cadence_days if len(gaps) > 1 else 0.0
    qty_spread = statistics.pstdev(qtys) / avg_qty if len(qtys) > 1 else 0.0
    wape = min(1.0, 0.35 + 0.35 * gap_spread + 0.30 * qty_spread)
    interval_error = total * min(wape, 1.0)
    if len(records) >= 7 and wape <= 0.25:
        confidence = "high"
    elif len(records) >= 4 and wape <= 0.55:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "daily": [daily] * len(future),
        "total": total,
        "low": max(0.0, total - interval_error),
        "high": total + interval_error,
        "safety": daily * (1.0 + min(wape, 1.0)),
        "model_key": "cadence",
        "model_name": "Purchase cadence",
        "wape": wape,
        "confidence": confidence,
        "scores": {"cadence": 1.0},
    }


def _features(history: list[float], target_date: date, index: int) -> list[float]:
    def lag(n):
        return history[-n] if len(history) >= n else _mean(history)

    last7 = history[-7:] or [0.0]
    last28 = history[-28:] or last7
    dow = [1.0 if target_date.weekday() == i else 0.0 for i in range(7)]
    return dow + [
        index / 100.0,
        lag(1), lag(7), lag(14),
        _mean(last7), _mean(last28),
        statistics.pstdev(last7) if len(last7) > 1 else 0.0,
    ]


def _seasonal_forecast(values: list[float], dates: list[date], future: list[date]) -> list[float]:
    overall = _mean(values[-28:])
    out = []
    for target in future:
        matches = [v for d, v in zip(dates, values) if d.weekday() == target.weekday()][-4:]
        out.append(max(0.0, _mean(matches) if matches else overall))
    return out


def _ewma_forecast(values: list[float], dates: list[date], future: list[date]) -> list[float]:
    level = values[0] if values else 0.0
    alpha = 0.28
    for value in values:
        level = alpha * value + (1 - alpha) * level
    recent = values[-28:]
    base = _mean(recent) or level
    factors = {}
    for dow in range(7):
        matched = [v for d, v in zip(dates[-28:], recent) if d.weekday() == dow]
        factors[dow] = (_mean(matched) / base) if matched and base else 1.0
    return [max(0.0, level * factors.get(d.weekday(), 1.0)) for d in future]


def _ml_forecast(kind: str, values: list[float], dates: list[date], future: list[date]) -> list[float] | None:
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return None

    if len(values) < 35 or sum(1 for value in values if value > 0) < 14:
        return None
    x, y = [], []
    for idx in range(28, len(values)):
        x.append(_features(values[:idx], dates[idx], idx))
        y.append(values[idx])
    if len(x) < 7:
        return None
    if kind == "ridge":
        model = make_pipeline(StandardScaler(), Ridge(alpha=3.0))
    else:
        model = RandomForestRegressor(
            n_estimators=20, max_depth=4, min_samples_leaf=3,
            random_state=42, n_jobs=1,
        )
    model.fit(x, y)
    extended = list(values)
    out = []
    for offset, target in enumerate(future):
        prediction = max(0.0, float(model.predict([_features(extended, target, len(values) + offset)])[0]))
        out.append(prediction)
        extended.append(prediction)
    return out


def _candidate(kind: str, values: list[float], dates: list[date], future: list[date]):
    if kind == "seasonal":
        return _seasonal_forecast(values, dates, future)
    if kind == "ewma":
        return _ewma_forecast(values, dates, future)
    return _ml_forecast(kind, values, dates, future)


def forecast_series(values: list[float], dates: list[date], future: list[date]) -> dict:
    """Select the lowest-error model using three held-out seven-day windows."""
    model_labels = {
        "seasonal": "Weekday seasonal",
        "ewma": "Recent-trend EWMA",
        "ridge": "Ridge ML",
        "forest": "Random forest ML",
    }
    scores: dict[str, float] = {}
    total_errors: dict[str, list[float]] = defaultdict(list)
    for kind in model_labels:
        absolute_error = actual_total = 0.0
        valid = True
        for holdout in (21, 14, 7):
            cutoff = len(values) - holdout
            if cutoff < 28:
                continue
            test_values = values[cutoff:cutoff + 7]
            test_dates = dates[cutoff:cutoff + 7]
            predicted = _candidate(kind, values[:cutoff], dates[:cutoff], test_dates)
            if predicted is None:
                valid = False
                break
            fold_error = sum(abs(a - p) for a, p in zip(test_values, predicted))
            absolute_error += fold_error
            actual_total += sum(abs(a) for a in test_values)
            total_errors[kind].append(abs(sum(test_values) - sum(predicted)))
        if valid:
            scores[kind] = absolute_error / actual_total if actual_total > 0 else absolute_error / 21.0

    if not scores:
        scores["seasonal"] = 1.0
    chosen = min(scores, key=scores.get)
    predicted = _candidate(chosen, values, dates, future) or [0.0] * len(future)
    total = sum(predicted)
    wape = scores[chosen]
    fold_errors = total_errors.get(chosen) or [total * min(wape, 1.0)]
    interval_error = sorted(fold_errors)[max(0, math.ceil(len(fold_errors) * 0.8) - 1)]
    average_day = total / len(future) if future else 0.0
    safety = average_day * (1.0 + min(wape, 1.0))
    if len(values) >= 49 and wape <= 0.25:
        confidence = "high"
    elif len(values) >= 35 and wape <= 0.55:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "daily": predicted,
        "total": total,
        "low": max(0.0, total - interval_error),
        "high": total + interval_error,
        "safety": safety,
        "model_key": chosen,
        "model_name": model_labels[chosen],
        "wape": wape,
        "confidence": confidence,
        "scores": scores,
    }


def _purchase_to_unit(qty, source: str, target: str, pack_size_g) -> float | None:
    q = float(qty)
    if source == target:
        return q
    factors = {
        ("g", "kg"): .001, ("kg", "g"): 1000,
        ("ml", "l"): .001, ("l", "ml"): 1000,
    }
    if (source, target) in factors:
        return q * factors[(source, target)]
    if target == "pcs" and pack_size_g:
        grams = q * 1000 if source == "kg" else q if source == "g" else None
        return grams / float(pack_size_g) if grams is not None else None
    return None


def round_to_increment(qty: float, increment: float) -> float:
    if qty <= 0:
        return 0.0
    if increment <= 0:
        raise ValueError("Order increment must be greater than zero.")
    q = Decimal(str(qty))
    inc = Decimal(str(increment))
    return float((q / inc).to_integral_value(rounding=ROUND_CEILING) * inc)


def split_delivery_quantities(approved_qty: float, daily: list[float], increment: float) -> list[float]:
    """Allocate one approved total across days 1, 3 and 5 without drift."""
    if approved_qty <= 0:
        return [0.0, 0.0, 0.0]
    groups = [sum(daily[0:2]), sum(daily[2:4]), sum(daily[4:7])]
    total_weight = sum(groups)
    if total_weight <= 0:
        groups = [2.0, 2.0, 3.0]
        total_weight = 7.0
    first = round_to_increment(approved_qty * groups[0] / total_weight, increment)
    second = round_to_increment(approved_qty * groups[1] / total_weight, increment)
    # Earlier round-ups may consume the full approved amount; the final drop is
    # the exact remainder so all three always reconcile to the approved order.
    if first > approved_qty:
        first = approved_qty
    if first + second > approved_qty:
        second = max(0.0, approved_qty - first)
    third = max(0.0, approved_qty - first - second)
    return [first, second, third]


def build_category_forecast(db, category: str = DEFAULT_WEEKLY_CATEGORY, horizon_start: date | None = None) -> dict:
    today = business_today()
    horizon_start = horizon_start or (today + timedelta(days=1))
    future = [horizon_start + timedelta(days=i) for i in range(7)]

    consolidated = category in {None, "", CONSOLIDATED_WEEKLY_CATEGORY, "all"}
    other_category = category == "Other"
    cadence_window_start = _shift_months(date(today.year, today.month, 1), -2)

    ingredient_sql = """
        SELECT id, name, unit::text AS unit, pack_size_g, order_increment_qty,
               COALESCE(category, 'Other') AS category
          FROM ingredients
         WHERE is_active
    """
    purchase_sql = """
        SELECT p.ingredient_id, p.qty, p.unit::text AS source_unit, p.total_price,
               p.purchase_date
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.usage_type = 'menu'
           AND i.is_active
    """
    params = {}
    if not consolidated:
        if other_category:
            ingredient_sql += " AND category IS NULL"
            purchase_sql += " AND i.category IS NULL"
        else:
            ingredient_sql += " AND category = :category"
            purchase_sql += " AND i.category = :category"
            params["category"] = category
    ingredient_sql += " ORDER BY COALESCE(category, 'Other'), name" if consolidated else " ORDER BY name"

    ingredients = db.execute(text(ingredient_sql), params).mappings().all()
    if not ingredients:
        raise ValueError(f"No active ingredients found for category {category!r}.")
    ingredient_by_id = {r["id"]: r for r in ingredients}

    sales = db.query(ItemSale).order_by(ItemSale.sale_date, ItemSale.id).all()
    sale_dates = sorted({s.sale_date for s in sales})
    usage, unresolved = calculate_ingredient_usage(db, sales)
    mapped_ids = {r[0] for r in db.execute(text("SELECT DISTINCT ingredient_id FROM ingredient_dish_map"))}

    purchase_rows = db.execute(text(purchase_sql), params).mappings().all()
    recent_cutoff = business_today() - timedelta(days=30)
    purchase_stats = defaultdict(lambda: {
        "qty": 0.0, "spend": 0.0, "priced_qty": 0.0,
        "recent_spend": 0.0, "recent_qty": 0.0,
    })
    cadence_history = defaultdict(list)
    for p in purchase_rows:
        ing = ingredient_by_id.get(p["ingredient_id"])
        if not ing:
            continue
        normalized = _purchase_to_unit(p["qty"], p["source_unit"], ing["unit"], ing["pack_size_g"])
        purchase_stats[p["ingredient_id"]]["spend"] += float(p["total_price"])
        if normalized is not None:
            purchase_stats[p["ingredient_id"]]["qty"] += normalized
            purchase_stats[p["ingredient_id"]]["priced_qty"] += normalized
            if p["purchase_date"] >= recent_cutoff:
                purchase_stats[p["ingredient_id"]]["recent_qty"] += normalized
                purchase_stats[p["ingredient_id"]]["recent_spend"] += float(p["total_price"])
            if p["purchase_date"] >= cadence_window_start:
                cadence_history[p["ingredient_id"]].append((p["purchase_date"], normalized))

    latest_stock = {
        r["ingredient_id"]: r for r in db.execute(text("""
            SELECT DISTINCT ON (ingredient_id) ingredient_id, on_hand_qty,
                   unit::text AS unit, counted_at
              FROM ingredient_stock
             ORDER BY ingredient_id, counted_at DESC, id DESC
        """)).mappings()
    }
    inbound = {r["ingredient_id"]: float(r["qty"] or 0) for r in db.execute(text("""
        SELECT l.ingredient_id, SUM(GREATEST(d.planned_qty - d.received_qty, 0)) AS qty
          FROM weekly_inventory_deliveries d
          JOIN weekly_inventory_order_lines l ON l.id = d.line_id
          JOIN weekly_inventory_orders o ON o.id = l.order_id
         WHERE o.status IN ('approved','partially_received')
           AND d.status IN ('planned','partial')
           AND d.delivery_date <= :horizon_end
         GROUP BY l.ingredient_id
    """), {"horizon_end": future[-1]}).mappings()}

    rows = []
    total_spend = sum(s["spend"] for s in purchase_stats.values())
    for ing in ingredients:
        values = [float(usage.get((day, ing["id"]), (Decimal("0"), ing["unit"]))[0]) for day in sale_dates]
        has_mapping = ing["id"] in mapped_ids
        stock = latest_stock.get(ing["id"])
        stock_compatible = bool(stock and stock["unit"] == ing["unit"])
        result = forecast_series(values, sale_dates, future) if has_mapping and sale_dates else None
        if result is None and not has_mapping:
            result = _cadence_forecast(cadence_history.get(ing["id"], []), future)
        stat = purchase_stats[ing["id"]]
        unit_cost = (
            stat["recent_spend"] / stat["recent_qty"] if stat["recent_qty"] > 0
            else stat["spend"] / stat["priced_qty"] if stat["priced_qty"] > 0
            else None
        )
        stock_qty = float(stock["on_hand_qty"]) if stock_compatible else None
        inbound_qty = inbound.get(ing["id"], 0.0)
        suggested = None
        if result and stock_qty is not None:
            suggested = max(0.0, result["total"] + result["safety"] - stock_qty - inbound_qty)
        configured_increment = float(ing["order_increment_qty"]) if ing["order_increment_qty"] else None
        if configured_increment:
            suggested_increment = configured_increment
        elif ing["unit"] == "kg" and ing["pack_size_g"]:
            suggested_increment = float(ing["pack_size_g"]) / 1000
        elif ing["unit"] == "kg":
            suggested_increment = 0.5
        elif ing["unit"] == "g":
            suggested_increment = 100.0
        else:
            suggested_increment = 1.0
        rounded = round_to_increment(suggested, suggested_increment) if suggested is not None else None
        reasons = []
        if result is None and not has_mapping:
            reasons.append("purchase cadence missing")
        if not stock_compatible:
            reasons.append("compatible stock count missing")
        row = {
            "ingredient_id": ing["id"], "name": ing["name"], "category": ing["category"] or "Other", "unit": ing["unit"],
            "historical_qty": stat["qty"], "historical_spend": stat["spend"],
            "spend_contribution_pct": (stat["spend"] / total_spend * 100) if total_spend else None,
            "forecast_qty": result["total"] if result else None,
            "forecast_low": result["low"] if result else None,
            "forecast_high": result["high"] if result else None,
            "safety_qty": result["safety"] if result else None,
            "daily_forecast": result["daily"] if result else [],
            "stock_qty": stock_qty, "stock_counted_at": stock["counted_at"] if stock_compatible else None,
            "inbound_qty": inbound_qty, "suggested_qty": rounded,
            "order_increment_qty": configured_increment,
            "suggested_increment": suggested_increment,
            "recent_unit_cost": unit_cost,
            "forecast_value": (result["total"] * unit_cost) if result and unit_cost is not None else 0.0,
            "model_name": result["model_name"] if result else None,
            "backtest_wape": result["wape"] if result else None,
            "confidence": result["confidence"] if result else "insufficient",
            "needs_input": bool(reasons), "input_reason": ", ".join(reasons),
            "diagnostics": {"model_scores": result["scores"] if result else {}, "unresolved_count": len(unresolved)},
        }
        rows.append(row)

    total_forecast_value = sum(r["forecast_value"] for r in rows)
    for row in rows:
        row["forecast_value_contribution_pct"] = (
            row["forecast_value"] / total_forecast_value * 100 if total_forecast_value else None
        )
    if consolidated:
        rows.sort(key=lambda r: (
            str(r["category"] or "Other").lower(),
            r["needs_input"],
            -(r["suggested_qty"] or 0),
            r["name"],
        ))
    else:
        rows.sort(key=lambda r: (r["needs_input"], -(r["suggested_qty"] or 0), r["name"]))
    return {
        "category": category, "horizon_start": future[0], "horizon_end": future[-1],
        "future_dates": future, "rows": rows, "model_version": MODEL_VERSION,
        "sales_start": sale_dates[0] if sale_dates else None,
        "sales_end": sale_dates[-1] if sale_dates else None,
        "sales_days": len(sale_dates),
        "category_count": len({r["category"] for r in rows}),
        "purchase_start": min((p["purchase_date"] for p in purchase_rows), default=None),
        "purchase_end": max((p["purchase_date"] for p in purchase_rows), default=None),
        "estimated_cost": sum((r["suggested_qty"] or 0) * (r["recent_unit_cost"] or 0) for r in rows),
    }
