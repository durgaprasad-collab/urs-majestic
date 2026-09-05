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


def _next_month_start(month_start):
    return (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _three_calendar_month_starts(today):
    first = _three_calendar_month_window_start(today)
    months = [first]
    for _ in range(2):
        months.append(_next_month_start(months[-1]))
    return months

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
    month_starts = _three_calendar_month_starts(today)
    monthly_metrics = []
    for month_start in month_starts:
        monthly_metrics.append({
            "month_start": month_start,
            "label": month_start.strftime("%B %Y"),
            "window_start": datetime.combine(month_start, time.min, tzinfo=tz),
            "window_end": datetime.combine(
                _next_month_start(month_start), time.min, tzinfo=tz
            ),
            "seconds": 0.0,
            "kg_used": decimal.Decimal("0"),
            "reading_count": 0,
        })

    # Chronological order to compute consumption deltas, then re-reverse for display.
    # recorded_at is stored UTC (DB default now()); convert to IST for both display
    # and the elapsed-time math below (elapsed-seconds between two aware instants
    # is timezone-invariant, so this only fixes what's shown, not the arithmetic).
    chrono = list(reversed(rows))
    last_in_use = None
    enriched = []
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
                # Split any interval crossing a month boundary proportionally so
                # each calendar month's usage stands on its own.
                if elapsed > 0:
                    for metric in monthly_metrics:
                        overlap_start = max(interval_start, metric["window_start"])
                        overlap_end = min(interval_end, metric["window_end"])
                        overlap_seconds = max(
                            (overlap_end - overlap_start).total_seconds(), 0.0
                        )
                        if overlap_seconds > 0:
                            metric["seconds"] += overlap_seconds
                            metric["kg_used"] += kg_used * decimal.Decimal(
                                str(overlap_seconds / elapsed)
                            )
                            metric["reading_count"] += 1
            else:
                d["kg_used"] = None
            last_in_use = d
        else:
            d["kg_used"] = None
        enriched.append(d)
    enriched.reverse()

    price_rows = db.execute(text(
        "select date_trunc('month', p.purchase_date)::date as month_start, "
        "avg(p.total_price) as average_cylinder_price "
        "from purchases p join ingredients i on i.id = p.ingredient_id "
        "where i.name = 'Cooking Gas' and p.deleted_at is null "
        "and p.purchase_date >= :window_start and p.purchase_date <= :today"
        " group by 1"
    ), {"window_start": month_starts[0], "today": today}).mappings().all()
    prices_by_month = {
        row["month_start"]: row["average_cylinder_price"] for row in price_rows
    }

    dish_rows = db.execute(text(
        "select month_start, avg(daily) as average_dishes_per_day from ("
        "select date_trunc('month', sale_date)::date as month_start, sale_date, "
        "sum(qty) as daily from item_sales "
        "where sale_date >= :window_start and sale_date <= :today "
        "group by 1, 2) daily_sales group by month_start"
    ), {"window_start": month_starts[0], "today": today}).mappings().all()
    dishes_by_month = {
        row["month_start"]: row["average_dishes_per_day"] for row in dish_rows
    }

    for metric in monthly_metrics:
        span_days = metric["seconds"] / 86400 if metric["seconds"] > 0 else None
        metric["span_days"] = span_days
        metric["avg_kg_per_day"] = (
            metric["kg_used"] / decimal.Decimal(str(span_days))
            if span_days and metric["kg_used"] > 0 else None
        )
        cylinder_price = prices_by_month.get(metric["month_start"])
        metric["cost_per_kg"] = (
            decimal.Decimal(str(cylinder_price)) / _NOMINAL_FULL_KG
            if cylinder_price else None
        )
        dishes_per_day = dishes_by_month.get(metric["month_start"])
        metric["cost_per_dish"] = None
        if (
            metric["avg_kg_per_day"] and metric["cost_per_kg"]
            and dishes_per_day and float(dishes_per_day) > 0
        ):
            metric["cost_per_dish"] = (
                metric["avg_kg_per_day"] * metric["cost_per_kg"]
                / decimal.Decimal(str(dishes_per_day))
            )

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
        "monthly_metrics": list(reversed(monthly_metrics)),
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
