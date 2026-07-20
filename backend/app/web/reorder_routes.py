"""Ingredient reorder forecast.

Reads `v_ingredient_reorder_forecast` — a purchase-cadence forecast of when each
menu ingredient is next due to be ordered and roughly how much. See migration
0012 for the method; the short version is: next order = last purchase + this
ingredient's own average gap between order dates, quantity = its average buy
size, both in the unit it is actually bought in.

This page is the human-facing view of that data. The same view is the intended
source for automated ordering later — everything shown here (date, quantity,
estimated cost, status) is a column, so the automation reads rows, not scraped
HTML.

Deliberately honest about its limits: at ~1 month of history these are cadence
estimates, not a fitted model, and ingredients bought on fewer than two distinct
days are shown separately as "not enough history" rather than forecast.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["reorder"])

# Buckets in the order the kitchen cares about them. Everything with a real
# cadence is ranked by how soon it is due; no-history items sink to their own
# section so they are visible but never mistaken for a due date.
_ACTION = {"overdue", "due", "soon"}

_SQL = text("""
    select *
    from v_ingredient_reorder_forecast
    where (:include_inactive or is_active)
    order by
      case status
        when 'overdue' then 0 when 'due' then 1 when 'soon' then 2
        when 'ok' then 3 else 4
      end,
      days_until_due nulls last,
      name
""")


@router.get("/order-forecast", response_class=HTMLResponse)
def order_forecast(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    include_inactive = request.query_params.get("inactive") == "1"
    # Default hides the steady "ok" items to keep the working list short; the
    # owner can switch to the full list to review everything.
    show_all = request.query_params.get("show") == "all"

    rows = db.execute(_SQL, {"include_inactive": include_inactive}).mappings().all()

    action, upcoming, no_history = [], [], []
    for r in rows:
        if r["status"] == "insufficient_history":
            no_history.append(r)
        elif r["status"] in _ACTION:
            action.append(r)
        else:  # ok
            upcoming.append(r)

    est_action_cost = sum(
        float(r["est_order_cost"]) for r in action if r["est_order_cost"] is not None
    )

    return _tmpl(request, "order_forecast.html", {
        "user": user,
        "action": action,
        "upcoming": upcoming,
        "no_history": no_history,
        "est_action_cost": est_action_cost,
        "include_inactive": include_inactive,
        "show_all": show_all,
        "generated_on": rows[0]["today"] if rows else None,
    })
