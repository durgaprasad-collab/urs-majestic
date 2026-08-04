"""Daily closing stock log — a full checklist across every ingredient category,
mirroring the paper "Daily Closing Stock Log" sheet used in the kitchen.

Deliberately reuses the ingredients / ingredient_stock tables the order
forecast already reads from, rather than a parallel catalog — every item on
the paper sheet (produce, dairy, dry goods, frozen, spices, packaging &
consumables, resale drinks) already exists as an ingredient row. One checklist
page writes counts for all of them in one save, and /stock-log/history is the
trace/audit log the paper binder used to be.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today, business_tz
from app.core.database import get_db
from app.web.deps import _tmpl, require_user
from app.web.reorder_routes import record_stock

router = APIRouter(tags=["stock-log"])

# Paper-sheet section order; anything else (e.g. Apparel) is appended after.
_CATEGORY_ORDER = [
    "Vegetables", "Dairy", "Dry Goods", "Frozen", "Spices", "Condiments",
    "Utilities", "Beverage - Resale", "Pulses",
]

_LIST_SQL = text("""
    select i.id as ingredient_id, i.name, i.unit, i.category, i.pack_size_g,
           s.on_hand_qty, s.counted_at
    from ingredients i
    left join lateral (
        select on_hand_qty, counted_at
        from ingredient_stock
        where ingredient_id = i.id
        order by counted_at desc
        limit 1
    ) s on true
    where i.is_active
    order by i.category nulls last, i.name
""")

_HISTORY_SQL = text("""
    select s.id, i.name, i.category, s.on_hand_qty, s.unit, s.counted_at,
           u.name as counted_by_name
    from ingredient_stock s
    join ingredients i on i.id = s.ingredient_id
    left join users u on u.id = s.counted_by
    order by s.counted_at desc
    limit 400
""")


@router.get("/stock-log", response_class=HTMLResponse)
def stock_log(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    rows = db.execute(_LIST_SQL).mappings().all()
    by_cat: dict[str, list] = {}
    for row in rows:
        r = dict(row)
        # counted_at is stored UTC (DB default now()); convert to IST for both
        # display and the "counted today" freshness check below.
        if r["counted_at"] is not None:
            r["counted_at"] = r["counted_at"].astimezone(business_tz())
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
        record_stock(db, ingredient_id, qty, unit, count_unit, getattr(user, "id", None))
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
