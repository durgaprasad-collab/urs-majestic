"""Reviewable seven-day inventory orders and delivery receiving."""

from datetime import date, timedelta
from decimal import Decimal
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.database import get_db
from app.models.purchase import Purchase
from app.services.weekly_ordering import (
    CONSOLIDATED_WEEKLY_CATEGORY, build_category_forecast, round_to_increment,
    split_delivery_quantities, weekly_forecast_categories,
)
from app.services.weekly_order_pdf import build_supplier_delivery_pdf
from app.services.weekly_order_pdf import build_weekly_summary_pdf
from app.web.deps import _tmpl, require_user


router = APIRouter(tags=["weekly-ordering"])


def _bucket_forecast_rows(rows: list[dict]):
    """Split forecast rows the same way everywhere they're shown (page + PDF):
    to_procure (urgent-first), no_action (well stocked), needs_input_rows."""
    to_procure = sorted(
        (r for r in rows if not r["needs_input"] and (r["suggested_qty"] or 0) > 0),
        key=lambda r: (not r["is_urgent"], -(r["suggested_qty"] or 0), r["name"]),
    )
    no_action = [r for r in rows if not r["needs_input"] and not (r["suggested_qty"] or 0) > 0]
    needs_input_rows = [r for r in rows if r["needs_input"]]
    urgent_count = sum(1 for r in to_procure if r["is_urgent"])
    return to_procure, no_action, needs_input_rows, urgent_count


@router.get("/weekly-order", response_class=HTMLResponse)
def weekly_order(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    categories = weekly_forecast_categories(db)
    selected_category = request.query_params.get("category")
    if selected_category in (None, "", "all", CONSOLIDATED_WEEKLY_CATEGORY):
        selected_category = CONSOLIDATED_WEEKLY_CATEGORY
    elif selected_category not in categories:
        selected_category = CONSOLIDATED_WEEKLY_CATEGORY
    forecast = build_category_forecast(db, category=selected_category)
    to_procure, no_action, needs_input_rows, urgent_count = _bucket_forecast_rows(forecast["rows"])
    return _tmpl(request, "weekly_order.html", {
        **forecast, "user": user,
        "to_procure": to_procure, "no_action": no_action, "needs_input_rows": needs_input_rows,
        "urgent_count": urgent_count,
        "categories": categories, "selected_category": selected_category,
        "selected_category_param": "all" if selected_category == CONSOLIDATED_WEEKLY_CATEGORY else selected_category,
        "consolidated_category": CONSOLIDATED_WEEKLY_CATEGORY,
        "error": request.query_params.get("error"),
    })


@router.post("/weekly-order/draft")
async def create_draft(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    form = await request.form()
    categories = weekly_forecast_categories(db)
    selected_category = form.get("category") or request.query_params.get("category") or CONSOLIDATED_WEEKLY_CATEGORY
    if selected_category in ("all", CONSOLIDATED_WEEKLY_CATEGORY, ""):
        selected_category = CONSOLIDATED_WEEKLY_CATEGORY
    elif selected_category not in categories:
        selected_category = CONSOLIDATED_WEEKLY_CATEGORY
    forecast = build_category_forecast(db, category=selected_category)
    order_id = db.execute(text("""
        INSERT INTO weekly_inventory_orders
            (category, horizon_start, horizon_end, status, model_version, created_by)
        VALUES (:category, :start, :end, 'draft', :version, :user_id)
        RETURNING id
    """), {
        "category": forecast["category"], "start": forecast["horizon_start"],
        "end": forecast["horizon_end"], "version": forecast["model_version"],
        "user_id": user.id,
    }).scalar_one()
    for row in forecast["rows"]:
        db.execute(text("""
            INSERT INTO weekly_inventory_order_lines
                (order_id, ingredient_id, unit, historical_qty, historical_spend,
                 spend_contribution_pct, forecast_value_contribution_pct,
                 forecast_qty, forecast_low, forecast_high, safety_qty, stock_qty, stock_counted_at,
                 inbound_qty, suggested_qty, order_increment_qty, recent_unit_cost,
                 model_name, backtest_wape, confidence, needs_input, input_reason,
                 daily_forecast, diagnostics)
            VALUES
                (:order_id, :ingredient_id, CAST(:unit AS unit_type), :historical_qty,
                 :historical_spend, :spend_pct, :value_pct, :forecast_qty, :forecast_low,
                 :forecast_high, :safety_qty, :stock_qty, :stock_counted_at, :inbound_qty, :suggested_qty,
                 :increment, :unit_cost, :model_name, :wape, :confidence, :needs_input,
                 :input_reason, CAST(:daily AS jsonb), CAST(:diagnostics AS jsonb))
        """), {
            "order_id": order_id, "ingredient_id": row["ingredient_id"], "unit": row["unit"],
            "historical_qty": row["historical_qty"], "historical_spend": row["historical_spend"],
            "spend_pct": row["spend_contribution_pct"],
            "value_pct": row["forecast_value_contribution_pct"],
            "forecast_qty": row["forecast_qty"], "forecast_low": row["forecast_low"],
            "forecast_high": row["forecast_high"], "safety_qty": row["safety_qty"],
            "stock_qty": row["stock_qty"], "stock_counted_at": row["stock_counted_at"],
            "inbound_qty": row["inbound_qty"],
            "suggested_qty": row["suggested_qty"], "increment": row["order_increment_qty"],
            "unit_cost": row["recent_unit_cost"], "model_name": row["model_name"],
            "wape": row["backtest_wape"], "confidence": row["confidence"],
            "needs_input": row["needs_input"], "input_reason": row["input_reason"],
            "daily": json.dumps(row["daily_forecast"]),
            "diagnostics": json.dumps(row["diagnostics"]),
        })
    db.commit()
    return RedirectResponse(f"/weekly-order/{order_id}", status_code=303)


@router.get("/weekly-order/summary.pdf")
def weekly_summary_pdf(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    categories = weekly_forecast_categories(db)
    selected_category = request.query_params.get("category")
    if selected_category in (None, "", "all", CONSOLIDATED_WEEKLY_CATEGORY):
        selected_category = CONSOLIDATED_WEEKLY_CATEGORY
    elif selected_category not in categories:
        selected_category = CONSOLIDATED_WEEKLY_CATEGORY
    forecast = build_category_forecast(db, category=selected_category)
    to_procure, no_action, needs_input_rows, urgent_count = _bucket_forecast_rows(forecast["rows"])
    try:
        payload = build_weekly_summary_pdf(forecast, to_procure, no_action, needs_input_rows, urgent_count)
    except ValueError as exc:
        return RedirectResponse(f"/weekly-order?error={quote(str(exc))}", status_code=303)
    filename = f"URS-Majestic-weekly-forecast-{selected_category.replace(' ', '-')}.pdf"
    return StreamingResponse(
        iter([payload]), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _order_context(db: Session, order_id: int):
    order = db.execute(text("""
        SELECT o.*, u.name AS created_by_name, a.name AS approved_by_name
          FROM weekly_inventory_orders o
          JOIN users u ON u.id = o.created_by
          LEFT JOIN users a ON a.id = o.approved_by
         WHERE o.id = :id
    """), {"id": order_id}).mappings().first()
    if not order:
        return None
    lines = db.execute(text("""
        SELECT l.*, i.name, COALESCE(i.category, 'Other') AS category, i.order_increment_qty AS configured_increment
          FROM weekly_inventory_order_lines l
          JOIN ingredients i ON i.id = l.ingredient_id
         WHERE l.order_id = :id
         ORDER BY l.needs_input, l.suggested_qty DESC NULLS LAST, i.name
    """), {"id": order_id}).mappings().all()
    deliveries = db.execute(text("""
        SELECT d.*, l.ingredient_id, l.unit, i.name,
               GREATEST(d.planned_qty - d.received_qty, 0) AS remaining_qty
          FROM weekly_inventory_deliveries d
          JOIN weekly_inventory_order_lines l ON l.id = d.line_id
          JOIN ingredients i ON i.id = l.ingredient_id
         WHERE l.order_id = :id
         ORDER BY d.delivery_date, i.name
    """), {"id": order_id}).mappings().all()
    return dict(order), [dict(x) for x in lines], [dict(x) for x in deliveries]


@router.get("/weekly-order/{order_id}", response_class=HTMLResponse)
def weekly_order_detail(order_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    context = _order_context(db, order_id)
    if not context:
        return RedirectResponse("/weekly-order?error=Order+not+found", status_code=303)
    order, lines, deliveries = context
    return _tmpl(request, "weekly_order_detail.html", {
        "user": user, "order": order, "lines": lines, "deliveries": deliveries,
        "error": request.query_params.get("error"), "saved": request.query_params.get("saved"),
        "today": business_today(),
    })


@router.get("/weekly-order/{order_id}/supplier.pdf")
def supplier_pdf(order_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    context = _order_context(db, order_id)
    if not context:
        return RedirectResponse("/weekly-order?error=Order+not+found", status_code=303)
    order, _lines, deliveries = context
    try:
        payload = build_supplier_delivery_pdf(order, deliveries)
    except ValueError as exc:
        return RedirectResponse(f"/weekly-order/{order_id}?error={quote(str(exc))}", status_code=303)
    filename = f"URS-Majestic-order-{order_id}-{order['horizon_start'].isoformat()}.pdf"
    return StreamingResponse(
        iter([payload]), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/weekly-order/{order_id}/approve")
async def approve_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    form = await request.form()
    context = _order_context(db, order_id)
    if not context:
        return RedirectResponse("/weekly-order?error=Order+not+found", status_code=303)
    order, lines, _ = context
    if order["status"] != "draft":
        return RedirectResponse(f"/weekly-order/{order_id}?error=Only+a+draft+can+be+approved", status_code=303)

    approved_lines = []
    errors = []
    for line in lines:
        try:
            qty = max(0.0, float(form.get(f"qty_{line['id']}") or 0))
            increment = float(form.get(f"increment_{line['id']}") or line["configured_increment"] or 0)
        except (TypeError, ValueError):
            errors.append(f"{line['name']}: enter valid quantities")
            continue
        if qty > 0 and line["needs_input"]:
            errors.append(f"{line['name']}: {line['input_reason']}")
            continue
        if qty > 0 and increment <= 0:
            errors.append(f"{line['name']}: supplier increment is required")
            continue
        rounded = round_to_increment(qty, increment) if qty > 0 else 0.0
        approved_lines.append((line, rounded, increment if increment > 0 else None))
    if errors:
        return RedirectResponse(
            f"/weekly-order/{order_id}?error={quote('; '.join(errors[:6]))}", status_code=303
        )

    for line, qty, increment in approved_lines:
        db.execute(text("""
            UPDATE weekly_inventory_order_lines
               SET approved_qty = :qty, order_increment_qty = :increment
             WHERE id = :id
        """), {"qty": qty, "increment": increment, "id": line["id"]})
        if increment:
            db.execute(text("UPDATE ingredients SET order_increment_qty=:inc WHERE id=:id"), {
                "inc": increment, "id": line["ingredient_id"],
            })
        if qty > 0:
            daily = [float(x) for x in (line["daily_forecast"] or [])]
            splits = split_delivery_quantities(qty, daily, increment)
            for offset, planned in zip((0, 2, 4), splits):
                if planned <= 0:
                    continue
                db.execute(text("""
                    INSERT INTO weekly_inventory_deliveries (line_id, delivery_date, planned_qty)
                    VALUES (:line_id, :delivery_date, :planned_qty)
                """), {
                    "line_id": line["id"],
                    "delivery_date": order["horizon_start"] + timedelta(days=offset),
                    "planned_qty": planned,
                })
    db.execute(text("""
        UPDATE weekly_inventory_orders
           SET status='approved', approved_by=:user_id, approved_at=now()
         WHERE id=:id
    """), {"user_id": user.id, "id": order_id})
    db.commit()
    return RedirectResponse(f"/weekly-order/{order_id}?saved=approved", status_code=303)


@router.post("/weekly-order/deliveries/{delivery_id}/receive")
async def receive_delivery(delivery_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    form = await request.form()
    row = db.execute(text("""
        SELECT d.*, l.order_id, l.ingredient_id, l.unit, i.name, o.status AS order_status
          FROM weekly_inventory_deliveries d
          JOIN weekly_inventory_order_lines l ON l.id=d.line_id
          JOIN weekly_inventory_orders o ON o.id=l.order_id
          JOIN ingredients i ON i.id=l.ingredient_id
         WHERE d.id=:id FOR UPDATE OF d
    """), {"id": delivery_id}).mappings().first()
    if not row:
        return RedirectResponse("/weekly-order?error=Delivery+not+found", status_code=303)
    order_id = row["order_id"]
    try:
        qty = Decimal(str(form.get("qty")))
        total_price = Decimal(str(form.get("total_price")))
        purchase_date = date.fromisoformat(str(form.get("purchase_date")))
    except Exception:
        return RedirectResponse(f"/weekly-order/{order_id}?error=Enter+valid+receiving+details", status_code=303)
    if qty <= 0 or total_price < 0:
        return RedirectResponse(f"/weekly-order/{order_id}?error=Received+quantity+must+be+positive", status_code=303)
    if row["order_status"] not in ("approved", "partially_received") or row["status"] == "cancelled":
        return RedirectResponse(f"/weekly-order/{order_id}?error=This+delivery+cannot+be+received", status_code=303)

    purchase = Purchase(
        ingredient_id=row["ingredient_id"], qty=qty.quantize(Decimal("0.001")),
        unit=row["unit"], total_price=total_price.quantize(Decimal("0.01")),
        purchase_date=purchase_date, usage_type="menu", entered_by_user_id=user.id,
        notes=f"Weekly order #{order_id}, delivery #{delivery_id}",
    )
    db.add(purchase)
    db.flush()
    new_received = Decimal(str(row["received_qty"])) + qty
    delivery_status = "received" if new_received >= Decimal(str(row["planned_qty"])) else "partial"
    db.execute(text("""
        UPDATE weekly_inventory_deliveries
           SET received_qty=:received, status=:status WHERE id=:id
    """), {"received": new_received, "status": delivery_status, "id": delivery_id})
    db.execute(text("""
        INSERT INTO weekly_inventory_receipts
            (delivery_id, purchase_id, qty, total_price, received_by)
        VALUES (:delivery_id, :purchase_id, :qty, :price, :user_id)
    """), {
        "delivery_id": delivery_id, "purchase_id": purchase.id, "qty": qty,
        "price": total_price, "user_id": user.id,
    })
    remaining = db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE d.status IN ('planned','partial')) AS open_count
          FROM weekly_inventory_deliveries d
          JOIN weekly_inventory_order_lines l ON l.id=d.line_id
         WHERE l.order_id=:order_id
    """), {"order_id": order_id}).scalar_one()
    db.execute(text("""
        UPDATE weekly_inventory_orders SET status=:status WHERE id=:id
    """), {"status": "received" if remaining == 0 else "partially_received", "id": order_id})
    db.commit()
    return RedirectResponse(f"/weekly-order/{order_id}?saved=received", status_code=303)


@router.post("/weekly-order/{order_id}/cancel")
def cancel_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    received = db.execute(text("""
        SELECT COUNT(*) FROM weekly_inventory_receipts r
        JOIN weekly_inventory_deliveries d ON d.id=r.delivery_id
        JOIN weekly_inventory_order_lines l ON l.id=d.line_id WHERE l.order_id=:id
    """), {"id": order_id}).scalar_one()
    if received:
        return RedirectResponse(f"/weekly-order/{order_id}?error=An+order+with+receipts+cannot+be+cancelled", status_code=303)
    db.execute(text("UPDATE weekly_inventory_orders SET status='cancelled' WHERE id=:id"), {"id": order_id})
    db.execute(text("""
        UPDATE weekly_inventory_deliveries SET status='cancelled'
         WHERE line_id IN (SELECT id FROM weekly_inventory_order_lines WHERE order_id=:id)
    """), {"id": order_id})
    db.commit()
    return RedirectResponse(f"/weekly-order/{order_id}?saved=cancelled", status_code=303)
