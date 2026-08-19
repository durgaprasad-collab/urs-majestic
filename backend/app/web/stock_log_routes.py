"""Daily closing stock log - a full checklist across every ingredient category.

This reuses the ingredients / ingredient_stock tables the order forecast
already reads from. One checklist page writes counts for all items in one save,
and /stock-log/history is the trace/audit log.
"""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today, business_tz
from app.core.database import get_db
from app.services.stock_log_pdf import build_low_cover_stock_pdf
from app.web.deps import _tmpl, require_user
from app.web.reorder_routes import record_stock, _LATEST_GAS_SQL, _GAS_AVERAGE_SQL

router = APIRouter(tags=["stock-log"])

# Paper-sheet section order; anything else (e.g. Apparel) is appended after.
_CATEGORY_ORDER = [
    "Vegetables", "Dairy", "Dry Goods", "Frozen", "Spices", "Condiments",
    "Utilities", "Beverage - Resale", "Pulses",
]

_LIST_SQL = text("""
    with purchase_edits as (
        select target_id, max(repaired_at) as edited_at
          from cost_base_repair_log
         where target_table = 'purchases'
         group by target_id
    ), purchase_events as (
        select p.ingredient_id,
               max(greatest(p.created_at, coalesce(e.edited_at, p.created_at))) as event_at
          from purchases p
          left join purchase_edits e on e.target_id = p.id
         where p.deleted_at is null
         group by p.ingredient_id
    )
    select i.id as ingredient_id, i.name, i.unit, i.category, i.pack_size_g,
           s.on_hand_qty, s.counted_at, s.note,
           v.daily_consumption,
           -- A stock count goes stale the moment a purchase lands after it --
           -- the shelf now holds more than the count says. Same rule Order
           -- Forecast uses (reorder_routes.py::_enrich) so the two pages
           -- can't disagree about whether a count is still trustworthy.
           case
             when s.on_hand_qty is not null
              and v.daily_consumption is not null
              and v.daily_consumption > 0
              and s.stock_unit = v.unit::text
              and (pe.event_at is null or s.counted_at > pe.event_at
                   or coalesce(s.note, '') like 'purchase_auto:%')
             then s.on_hand_qty / v.daily_consumption
             else null
           end as cover_days,
           (pe.event_at is not null and s.counted_at is not null
            and s.counted_at <= pe.event_at
            and coalesce(s.note, '') not like 'purchase_auto:%') as stock_stale
    from ingredients i
    left join lateral (
        select on_hand_qty, unit::text as stock_unit, counted_at, note
        from ingredient_stock
        where ingredient_id = i.id
        order by counted_at desc
        limit 1
    ) s on true
    left join v_ingredient_reorder_forecast v on v.ingredient_id = i.id
    left join purchase_events pe on pe.ingredient_id = i.id
    where i.is_active
    order by i.category nulls last, i.name
""")

_HISTORY_SQL = text("""
    select s.id, i.name, i.category, s.on_hand_qty, s.unit, s.counted_at, s.note,
           u.name as counted_by_name
    from ingredient_stock s
    join ingredients i on i.id = s.ingredient_id
    left join users u on u.id = s.counted_by
    order by s.counted_at desc
    limit 400
""")


def _stock_rows(db: Session) -> list[dict]:
    rows = db.execute(_LIST_SQL).mappings().all()
    gas_reading = db.execute(_LATEST_GAS_SQL).mappings().first()
    gas_avg_per_day = db.execute(_GAS_AVERAGE_SQL).scalar()
    decorated: list[dict] = []
    for row in rows:
        r = dict(row)
        # counted_at is stored UTC (DB default now()); convert to IST for both
        # display and the "counted today" freshness check below.
        if r["counted_at"] is not None:
            r["counted_at"] = r["counted_at"].astimezone(business_tz())
        r["cover_qty"] = r["on_hand_qty"]
        cover = float(r["cover_days"]) if r["cover_days"] is not None else None
        if (
            r["name"] == "Cooking Gas" and gas_reading
            and gas_avg_per_day is not None and gas_avg_per_day > 0
        ):
            r["cover_qty"] = gas_reading["net_kg"]
            r["daily_consumption"] = gas_avg_per_day
            cover = float(gas_reading["net_kg"] / gas_avg_per_day)
            r["cover_source"] = "gas log"
        else:
            r["cover_source"] = "stock log"
        r["cover_days"] = cover
        r["cover_colour"] = (
            "red" if cover is not None and cover < 3
            else "orange" if cover is not None and cover < 7
            else "green" if cover is not None
            else "neutral"
        )
        decorated.append(r)
    return decorated


@router.get("/stock-log", response_class=HTMLResponse)
def stock_log(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    rows = _stock_rows(db)
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"] or "Other", []).append(r)

    ordered = [(c, by_cat[c]) for c in _CATEGORY_ORDER if c in by_cat]
    ordered += [(c, rs) for c, rs in by_cat.items() if c not in _CATEGORY_ORDER]

    saved = request.query_params.get("saved")
    return _tmpl(request, "stock_log.html", {
        "user": user,
        "categories": ordered,
        "saved": int(saved) if saved and saved.isdigit() else None,
        "today": business_today(),
    })


@router.get("/stock-log/low-cover.pdf")
def stock_log_low_cover_pdf(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    rows = _stock_rows(db)
    payload = build_low_cover_stock_pdf(rows)
    filename = f"URS-Majestic-stock-log-low-cover-{business_today().isoformat()}.pdf"
    return StreamingResponse(
        iter([payload]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/stock-log/count")
async def save_stock_log(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    form = await request.form()
    saved = 0
    for key, value in form.multi_items():
        if not key.startswith("qty_") or value in (None, ""):
            continue
        try:
            ingredient_id = int(key.removeprefix("qty_"))
            qty = float(value)
        except ValueError:
            continue
        if qty < 0:
            continue
        unit = form.get(f"unit_{ingredient_id}")
        if not unit:
            continue
        count_unit = form.get(f"count_unit_{ingredient_id}") or unit
        note = "reorder_required" if form.get(f"reorder_{ingredient_id}") == "1" else None
        record_stock(db, ingredient_id, qty, unit, count_unit, getattr(user, "id", None), note)
        saved += 1

    db.commit()
    return RedirectResponse(url=f"/stock-log?saved={saved}", status_code=303)


@router.get("/stock-log/history", response_class=HTMLResponse)
def stock_log_history(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    rows = [dict(r) for r in db.execute(_HISTORY_SQL).mappings().all()]
    for r in rows:
        r["counted_at"] = r["counted_at"].astimezone(business_tz())
    return _tmpl(request, "stock_log_history.html", {"user": user, "rows": rows})
