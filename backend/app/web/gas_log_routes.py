"""Gas cylinder weight log -- measured LPG consumption to replace the flat
OVERHEAD_PER_DISH guess in the cost engine with a real number, once a trend
exists.

Only the in-use cylinder is actually being drawn from, so consumption is
computed strictly between consecutive 'in_use' readings that aren't marked
is_new_cylinder (a swap/refill breaks the chain -- a fresh full cylinder must
never read as "gas appeared"). The spare's readings are informational only.

This page is data collection only: nothing in the cost engine reads this
table yet. Once there's a real multi-day trend, a follow-up derives gas cost
per dish from it, the same way cost_engine._parcel_rate() replaced a flat
packaging average with a measured one.
"""
import decimal
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_tz
from app.core.database import get_db
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["gas-log"])

# Used only until a reading is ever marked is_new_cylinder -- the printed
# capacity of the commercial cylinders in use, before any of them have been
# weighed full.
_NOMINAL_FULL_KG = decimal.Decimal("19.2")


def _three_calendar_month_window_start(today):
    """Return the first day of the current month and its two predecessors."""
    month_start = today.replace(day=1)
    for _ in range(2):
        month_start = (month_start - timedelta(days=1)).replace(day=1)
    return month_start

_READINGS_SQL = text("""
    select gr.id, gr.recorded_at, gr.cylinder_role, gr.gross_kg, gr.tare_kg,
           (gr.gross_kg - gr.tare_kg) as net_kg, gr.is_new_cylinder, gr.note,
           u.name as recorded_by_name
    from gas_readings gr
    left join users u on u.id = gr.recorded_by
    order by gr.recorded_at desc
    limit 200
""")


@router.get("/gas-log", response_class=HTMLResponse)
def gas_log(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    rows = list(db.execute(_READINGS_SQL).mappings().all())

    tz = business_tz()
    today = datetime.now(tz).date()
    window_start_date = _three_calendar_month_window_start(today)
    metric_window_start = datetime.combine(window_start_date, time.min, tzinfo=tz)

    # Chronological order to compute consumption deltas, then re-reverse for display.
    # recorded_at is stored UTC (DB default now()); convert to IST for both display
    # and the elapsed-time math below (elapsed-seconds between two aware instants
    # is timezone-invariant, so this only fixes what's shown, not the arithmetic).
    chrono = list(reversed(rows))
    last_in_use = None
    enriched = []
    delta_seconds_total = 0.0
    total_kg_used = decimal.Decimal("0")
    reading_count = 0
    for r in chrono:
        d = dict(r)
        d["recorded_at"] = d["recorded_at"].astimezone(tz)
        if d["cylinder_role"] == "in_use":
            if last_in_use is not None:
                if d["is_new_cylinder"]:
                    # A swap closes the old cylinder and starts a new 19.2 kg
                    # cylinder that may already have been used before weighing.
                    # Count both the old remainder and the new-cylinder usage.
                    kg_used = last_in_use["net_kg"] + max(
                        _NOMINAL_FULL_KG - d["net_kg"], decimal.Decimal("0")
                    )
                else:
                    kg_used = max(
                        last_in_use["net_kg"] - d["net_kg"], decimal.Decimal("0")
                    )
                interval_start = last_in_use["recorded_at"]
                interval_end = d["recorded_at"]
                elapsed = (interval_end - interval_start).total_seconds()
                d["kg_used"] = kg_used
                # Metrics use the current calendar month plus the preceding two.
                # If the boundary cuts through an interval, allocate consumption
                # proportionally to the part of the interval inside the window.
                overlap_start = max(interval_start, metric_window_start)
                overlap_seconds = max((interval_end - overlap_start).total_seconds(), 0.0)
                if elapsed > 0 and overlap_seconds > 0:
                    delta_seconds_total += overlap_seconds
                    total_kg_used += kg_used * decimal.Decimal(str(overlap_seconds / elapsed))
                    reading_count += 1
            else:
                d["kg_used"] = None
            last_in_use = d
        else:
            d["kg_used"] = None
        enriched.append(d)
    enriched.reverse()

    span_days = (delta_seconds_total / 86400) if delta_seconds_total > 0 else None
    avg_kg_per_day = (
        total_kg_used / decimal.Decimal(str(span_days))
        if span_days and total_kg_used > 0 else None
    )

    average_cylinder_price = db.execute(text(
        "select avg(total_price) from purchases p join ingredients i on i.id = p.ingredient_id "
        "where i.name = 'Cooking Gas' and p.deleted_at is null "
        "and p.purchase_date >= :window_start and p.purchase_date <= :today"
    ), {"window_start": metric_window_start.date(), "today": today}).scalar()
    full_kg = _NOMINAL_FULL_KG
    cost_per_kg = (
        decimal.Decimal(str(average_cylinder_price)) / full_kg
        if average_cylinder_price else None
    )

    cost_per_day = (cost_per_kg * avg_kg_per_day) if (cost_per_kg and avg_kg_per_day) else None

    avg_dishes_per_day = db.execute(text(
        "select avg(daily) from (select sale_date, sum(qty) as daily from item_sales "
        "where sale_date >= :window_start and sale_date <= :today group by sale_date) t"
    ), {"window_start": metric_window_start.date(), "today": today}).scalar()
    cost_per_dish = None
    if cost_per_day and avg_dishes_per_day and float(avg_dishes_per_day) > 0:
        cost_per_dish = cost_per_day / decimal.Decimal(str(avg_dishes_per_day))

    # `enriched` is newest-first for display, so the first matching role is the
    # current reading. Reversing here previously made the summary show the
    # oldest cylinder value while History correctly showed the newest one.
    latest_in_use = next((d for d in enriched if d["cylinder_role"] == "in_use"), None)
    latest_spare = next((d for d in enriched if d["cylinder_role"] == "spare"), None)

    return _tmpl(request, "gas_log.html", {
        "user": user,
        "readings": enriched,
        "latest_in_use": latest_in_use,
        "latest_spare": latest_spare,
        "total_kg_used": total_kg_used,
        "span_days": span_days,
        "avg_kg_per_day": avg_kg_per_day,
        "cost_per_kg": cost_per_kg,
        "full_kg": full_kg,
        "cost_per_day": cost_per_day,
        "cost_per_dish": cost_per_dish,
        "reading_count": reading_count,
        "metric_window_label": (
            f"{metric_window_start.strftime('%b')}\u2013{today.strftime('%b %Y')}"
        ),
    })


@router.post("/gas-log/reading")
def save_reading(
    request: Request,
    cylinder_role: str = Form(...),
    gross_kg: float = Form(...),
    tare_kg: float = Form(20.0),
    is_new_cylinder: str = Form(None),
    note: str = Form(None),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    if cylinder_role not in ("in_use", "spare"):
        return RedirectResponse(url="/gas-log", status_code=303)

    db.execute(
        text(
            "INSERT INTO gas_readings (cylinder_role, gross_kg, tare_kg, is_new_cylinder, recorded_by, note) "
            "VALUES (:role, :gross, :tare, :is_new, :by, :note)"
        ),
        {
            "role": cylinder_role, "gross": gross_kg, "tare": tare_kg,
            "is_new": bool(is_new_cylinder), "by": getattr(user, "id", None), "note": note or None,
        },
    )
    db.commit()
    return RedirectResponse(url="/gas-log", status_code=303)
