"""Kitchen prep sheet.

Reads `v_prep_sheet` — trailing-28-day descriptive statistics per menu item per
day-of-week, with zero-sale open days counted as zero.

This is deliberately NOT a forecast. At the current volume (~36 units/day, CV
~65%, top item under 4/day, 56 of 75 items below 0.5/day) a fitted model would
produce intervals wider than the quantities themselves. The page shows observed
means and observed maxima and lets the kitchen apply judgement. Revisit the
forecasting question only after ~12 weeks of history with no pricing/offer
changes inside the window.

The deliverable is the printout, not the screen. Sayee reads paper.
"""
from datetime import timedelta

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.database import get_db
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["prep"])

# Postgres extract(dow) is 0=Sunday..6=Saturday.
_DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

_BANDS = {
    "kitchen": ("core", "occasional"),   # default: what actually gets prepped
    "core": ("core",),
    "all": ("core", "occasional", "tail"),
}

_SQL = text("""
    select
      p.menu_item_id,
      p.item,
      m.category,
      p.avg_7d,
      p.max_7d,
      p.days_sold_7d,
      p.avg_28d,
      p.avg_same_dow,
      p.max_same_dow,
      p.dow_samples,
      p.prep_qty_suggested,
      p.prep_qty_upper,
      p.trend_pct_vs_prior_7d,
      p.demand_band
    from v_prep_sheet p
    join menu_items m on m.id = p.menu_item_id
    where p.dow = :dow
      and p.demand_band = any(:bands)
      and (:include_non_food or m.is_food)
      -- An item with nothing to prep is noise on a printed sheet. Items that sold
      -- zero in the last 7 days AND zero on this weekday are dropped unless the
      -- user explicitly asks for the full list.
      and (:show_zero or p.prep_qty_suggested > 0)
    -- Ordered by the number Sayee acts on. Sorting by same-weekday average would
    -- bury a 1.0/day item below a 0.25/day one purely on a 4-sample weekday quirk.
    order by p.prep_qty_suggested desc, p.avg_7d desc, p.avg_same_dow desc, p.item
""")


@router.get("/prep-sheet", response_class=HTMLResponse)
def prep_sheet(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    # Default target = tomorrow in restaurant local time. Prep is decided the
    # night before, so "today" is the wrong default.
    tomorrow = business_today() + timedelta(days=1)
    raw_dow = request.query_params.get("dow", "")
    if raw_dow.isdigit() and 0 <= int(raw_dow) <= 6:
        dow = int(raw_dow)
    else:
        dow = int(tomorrow.strftime("%w"))

    band_key = request.query_params.get("band", "kitchen")
    if band_key not in _BANDS:
        band_key = "kitchen"
    include_non_food = request.query_params.get("non_food") == "1"

    rows = db.execute(_SQL, {
        "dow": dow,
        "bands": list(_BANDS[band_key]),
        "include_non_food": include_non_food,
        "show_zero": band_key == "all",
    }).mappings().all()

    # How much history actually backs this page. Shown on the printout so nobody
    # mistakes four observations for a pattern.
    meta = db.execute(text("""
        select min(sale_date) as first_day,
               max(sale_date) as last_day,
               count(distinct sale_date) as open_days
        from item_sales
    """)).mappings().first()

    return _tmpl(request, "prep_sheet.html", {
        "user": user,
        "rows": rows,
        "dow": dow,
        "dow_name": _DOW_NAMES[dow],
        "is_tomorrow": dow == int(tomorrow.strftime("%w")),
        "dow_names": list(enumerate(_DOW_NAMES)),
        "band_key": band_key,
        "include_non_food": include_non_food,
        "meta": meta,
        "generated_on": business_today(),
    })
