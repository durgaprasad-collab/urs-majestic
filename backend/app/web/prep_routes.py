"""Kitchen prep sheet — the next 3 days, side by side.

Reads `v_prep_sheet` — trailing-28-day descriptive statistics per menu item per
day-of-week, with zero-sale open days counted as zero. Each column is one upcoming
day; the numbers in it are that weekday's statistics. Prep is still a per-day
figure — the three columns let the kitchen plan a little ahead (and shop for
short-shelf-life items) without clicking through one day at a time.

Starts from TODAY, not tomorrow: the current day's morning prep is still to be
done, so dropping today would hide the day the kitchen is about to cook for. Past
IST midnight, column one is the new day and yesterday falls off — that is correct.

This is deliberately NOT a forecast. At the current volume (~36 units/day, CV
~65%, top item under 4/day, 56 of 75 items below 0.5/day) a fitted model would
produce intervals wider than the quantities themselves. The page shows observed
means and observed maxima and lets the kitchen apply judgement.

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

_HORIZON = 3  # days shown, starting today

_SQL = text("""
    select
      p.menu_item_id,
      p.item,
      m.category,
      p.dow,
      p.avg_7d,
      p.avg_same_dow,
      p.max_same_dow,
      p.dow_samples,
      p.prep_qty_suggested,
      p.prep_qty_upper,
      p.trend_pct_vs_prior_7d,
      p.demand_band
    from v_prep_sheet p
    join menu_items m on m.id = p.menu_item_id
    where p.dow = any(:dows)
      and p.demand_band = any(:bands)
      and (:include_non_food or m.is_food)
""")


@router.get("/prep-sheet", response_class=HTMLResponse)
def prep_sheet(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    band_key = request.query_params.get("band", "kitchen")
    if band_key not in _BANDS:
        band_key = "kitchen"
    include_non_food = request.query_params.get("non_food") == "1"
    # Only the full "everything" band keeps items with nothing to prep — on the
    # working sheet a zero row is noise.
    show_zero = band_key == "all"

    today = business_today()
    days = [today + timedelta(days=i) for i in range(_HORIZON)]
    # PG extract(dow): 0=Sun..6=Sat — matches strftime("%w").
    day_cols = [
        {
            "date": d,
            "dow": int(d.strftime("%w")),
            "name": _DOW_NAMES[int(d.strftime("%w"))],
            "short": _DOW_NAMES[int(d.strftime("%w"))][:3],
            "is_today": i == 0,
        }
        for i, d in enumerate(days)
    ]

    rows = db.execute(_SQL, {
        "dows": sorted({c["dow"] for c in day_cols}),
        "bands": list(_BANDS[band_key]),
        "include_non_food": include_non_food,
    }).mappings().all()

    # Pivot the (item, dow) rows into one entry per item with a cell per column.
    by_item: dict[int, dict] = {}
    for r in rows:
        entry = by_item.setdefault(r["menu_item_id"], {
            "item": r["item"],
            "category": r["category"],
            # demand_band and trend are overall (not weekday-specific) in the
            # view, so they are the same across an item's rows.
            "demand_band": r["demand_band"],
            "trend": r["trend_pct_vs_prior_7d"],
            "by_dow": {},
        })
        entry["by_dow"][r["dow"]] = r

    items: list[dict] = []
    for entry in by_item.values():
        cells, total, peak = [], 0.0, 0.0
        for col in day_cols:
            dr = entry["by_dow"].get(col["dow"])
            if dr is None:
                cells.append(None)   # restaurant had no open day on this weekday
                continue
            qty = float(dr["prep_qty_suggested"] or 0)
            upper = float(dr["prep_qty_upper"] or 0)
            cells.append({
                "qty": qty,
                "upper": upper,
                "avg_same_dow": float(dr["avg_same_dow"] or 0),
                "dow_samples": dr["dow_samples"],
            })
            total += qty
            peak = max(peak, upper)
        items.append({
            "item": entry["item"],
            "category": entry["category"],
            "demand_band": entry["demand_band"],
            "trend": entry["trend"],
            "cells": cells,
            "total": total,
            "peak": peak,
        })

    if not show_zero:
        items = [it for it in items if it["total"] > 0]

    # Ranked by the number Sayee acts on across the window.
    items.sort(key=lambda x: (-x["total"], -x["peak"], x["item"]))

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
        "items": items,
        "day_cols": day_cols,
        "horizon": _HORIZON,
        "band_key": band_key,
        "include_non_food": include_non_food,
        "meta": meta,
        "generated_on": today,
    })
