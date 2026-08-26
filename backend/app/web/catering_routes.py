"""Catering order listing -- confirmed catering bookings taken outside the
regular POS/order flow. See app/models/catering_order.py for why these live
in standalone tables rather than as a discriminator on orders/order_items.
"""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models.catering_order import CateringOrder
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["catering"])


@router.get("/catering-orders", response_class=HTMLResponse)
def catering_orders_list(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    orders = (
        db.query(CateringOrder)
        .options(selectinload(CateringOrder.items))
        .order_by(CateringOrder.delivery_date.desc(), CateringOrder.delivery_time.desc())
        .all()
    )
    return _tmpl(request, "catering_orders.html", {
        "user": user,
        "orders": orders,
    })
