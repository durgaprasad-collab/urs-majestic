"""Ingredient reorder forecast.

Reads `v_ingredient_reorder_forecast` — a per-ingredient forecast of when each
menu ingredient is next due to be ordered and roughly how much.

Two signals, in priority order per row:
  1. On-hand stock. If the owner has entered a current count (ingredient_stock,
     surfaced as the view's stock_* columns) and the ingredient has a usage
     rate, the row is driven by `remaining / daily_use` — real runout, not a
     guess. This is ground truth and overrides cadence.
  2. Purchase cadence. With no count, it falls back to last purchase + this
     ingredient's average gap between order dates (migration 0012), quantity =
     its average buy size, in the unit it is bought in.

The same view is the intended source for automated ordering later — every
number shown is a column, so automation reads rows, not scraped HTML.
"""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from decimal import Decimal

from app.core.database import get_db
from app.core.clock import business_tz
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["reorder"])

_ACTION = {"overdue", "due", "soon"}

_SQL = text("""
    select *
    from v_ingredient_reorder_forecast
    where (:include_inactive or is_active)
""")

_GAS_REORDER_KG = Decimal("10")
_LATEST_GAS_SQL = text("""
    SELECT recorded_at, gross_kg, tare_kg, (gross_kg - tare_kg) AS net_kg
      FROM gas_readings
     WHERE cylinder_role = 'in_use'
     ORDER BY recorded_at DESC
     LIMIT 1
""")


def _enrich(row) -> dict:
    """Attach effective (stock-preferred, else cadence) fields for display."""
    d = dict(row)
    # A stock count goes STALE the moment a purchase lands after it -- the shelf
    # now holds more than the count says. In that case ignore the count and fall
    # back to cadence, which already treats a just-bought item as recently
    # purchased (so a fresh buy stops it showing as "due").
    counted = d.get("stock_counted_on")
    last_buy = d.get("last_purchase_any")
    stock_fresh = counted is not None and (last_buy is None or counted >= last_buy)
    has_stock = (d.get("on_hand_qty") is not None
                 and d.get("stock_status") is not None
                 and stock_fresh)
    d["has_stock"] = has_stock
    if has_stock:
        d["eff_status"] = d["stock_status"]
        d["eff_days_until_due"] = d["stock_days_until_due"]
        d["eff_cover_left"] = d["stock_days_cover_left"]
        d["eff_order_date"] = d["stock_runout_date"]
    else:
        # `days_cover_left` is the original batch duration and never decreases.
        # Current cover is the time remaining from today to the estimated runout.
        runout = d.get("runout_date")
        today = d.get("today")
        remaining_cover = (runout - today).days if runout is not None and today is not None else None
        d["eff_cover_left"] = Decimal(remaining_cover) if remaining_cover is not None else None
        d["eff_order_date"] = runout
        d["eff_days_until_due"] = remaining_cover
        if remaining_cover is None:
            d["eff_status"] = d["status"]
        elif remaining_cover <= 0:
            d["eff_status"] = "due"
        elif remaining_cover < 3:
            d["eff_status"] = "soon"
        else:
            d["eff_status"] = "ok"
    return d


def _apply_gas_log(d: dict, gas_reading) -> dict:
    """Replace Cooking Gas cadence with the latest measured in-use cylinder."""
    d["is_gas"] = d.get("name") == "Cooking Gas"
    if not d["is_gas"]:
        return d
    d["gas_reorder_kg"] = _GAS_REORDER_KG
    d["gas_net_kg"] = gas_reading["net_kg"] if gas_reading else None
    d["gas_gross_kg"] = gas_reading["gross_kg"] if gas_reading else None
    d["gas_tare_kg"] = gas_reading["tare_kg"] if gas_reading else None
    d["gas_recorded_at"] = (
        gas_reading["recorded_at"].astimezone(business_tz()) if gas_reading else None
    )
    d["eff_status"] = "due" if d["gas_net_kg"] is not None and d["gas_net_kg"] < _GAS_REORDER_KG else "ok"
    d["eff_days_until_due"] = None
    d["eff_cover_left"] = None
    d["eff_order_date"] = d.get("today") if d["eff_status"] == "due" else None
    return d


def _needs_order(r: dict) -> bool:
    if r.get("is_gas"):
        return r.get("gas_net_kg") is not None and r["gas_net_kg"] < _GAS_REORDER_KG
    cover = r.get("eff_cover_left")
    return cover is not None and cover < 3


@router.get("/order-forecast", response_class=HTMLResponse)
def order_forecast(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    include_inactive = request.query_params.get("inactive") == "1"
    gas_reading = db.execute(_LATEST_GAS_SQL).mappings().first()
    rows = [
        _apply_gas_log(_enrich(r), gas_reading)
        for r in db.execute(_SQL, {"include_inactive": include_inactive}).mappings().all()
    ]

    action, upcoming, no_history, recently_bought = [], [], [], []
    for r in rows:
        if r.get("is_gas"):
            (action if _needs_order(r) else upcoming).append(r)
            continue
        cover = r["eff_cover_left"]
        if cover is not None:
            # One explicit gate: populate only when cover is strictly under 3 days.
            (action if cover < 3 else upcoming).append(r)
        elif r["has_stock"]:
            # A physical count is ground truth — it beats cadence AND the
            # recently-bought skip (you can buy and still be low, or vice versa).
            (action if r["eff_status"] in _ACTION else upcoming).append(r)
        elif r["recently_purchased"]:
            recently_bought.append(r)
        elif r["status"] in _ACTION:
            no_history.append(r)
        else:
            no_history.append(r)

    # Most urgent first, by effective days-until-due.
    _key = lambda r: (r["eff_days_until_due"] if r["eff_days_until_due"] is not None else 9999, r["name"])
    action.sort(key=_key)
    upcoming.sort(key=_key)

    est_action_cost = sum(
        float(r["est_order_cost"]) for r in action if r["est_order_cost"] is not None
    )

    return _tmpl(request, "order_forecast.html", {
        "user": user,
        "action": action,
        "upcoming": upcoming,
        "no_history": no_history,
        "recently_bought": recently_bought,
        "est_action_cost": est_action_cost,
        "include_inactive": include_inactive,
        "generated_on": rows[0]["today"] if rows else None,
    })


def record_stock(
    db: Session,
    ingredient_id: int,
    qty: float,
    unit: str,
    count_unit: str | None,
    counted_by: int | None,
    note: str | None = None,
) -> None:
    """Insert one on-hand count row, in the ingredient's forecast (primary)
    unit so the view can divide by daily_consumption directly. Shared by the
    single-row form on this page and the bulk stock-log checklist. Caller
    commits."""
    count_unit = count_unit or unit
    on_hand = max(qty, 0.0)

    units = db.execute(
        text(
            "SELECT i.pack_size_g, COALESCE(v.unit::text, i.unit::text) AS forecast_unit "
            "FROM ingredients i LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id "
            "WHERE i.id = :i"
        ),
        {"i": ingredient_id},
    ).mappings().one()
    forecast_unit = units["forecast_unit"] or unit

    # Packets -> primary unit via pack_size_g (grams/packet). Only weight units
    # convert; a volume primary unit with 'packet' should never happen.
    if count_unit == "packet":
        pack_g = units["pack_size_g"]
        if pack_g:
            on_hand = on_hand * float(pack_g)
            count_unit = "g"

    conversions = {
        ("kg", "g"): 1000.0,
        ("g", "kg"): 0.001,
        ("l", "ml"): 1000.0,
        ("ml", "l"): 0.001,
    }
    on_hand *= conversions.get((count_unit, forecast_unit), 1.0)

    db.execute(
        text(
            "INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note) "
            "VALUES (:i, :q, :u, :by, :note)"
        ),
        {"i": ingredient_id, "q": on_hand, "u": forecast_unit, "by": counted_by, "note": note},
    )


@router.post("/order-forecast/stock")
def save_stock(
    request: Request,
    ingredient_id: int = Form(...),
    qty: float = Form(...),
    unit: str = Form(...),
    count_unit: str = Form(None),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    record_stock(db, ingredient_id, qty, unit, count_unit, getattr(user, "id", None))
    db.commit()
    return RedirectResponse(url="/order-forecast", status_code=303)
