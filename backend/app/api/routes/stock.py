"""Mobile stock entry: staff key in on-hand counts, focused on items the
owner's Order Forecast page says need action. Reuses the exact same
"action" bucket the web /order-forecast page computes (cadence-only
ingredients included, not just ones with a physical count) and the same
insert helper for saving a count -- this is a second front door onto the
same tables, not a parallel forecast model.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.device_token import DeviceToken
from app.models.user import User
from app.schemas import DeviceTokenRegister, LowStockItem, StockCountCreate
from app.api.routes.requisitions import _EXCLUDED_ITEM_NAMES
from app.web.reorder_routes import compute_forecast_buckets, record_stock

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get("/low", response_model=list[LowStockItem])
def low_stock(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    action_rows = compute_forecast_buckets(db)["action"]
    return [
        LowStockItem(
            ingredient_id=r["ingredient_id"],
            name=r["name"],
            category=r["category"],
            unit=r["unit"],
            on_hand_qty=r.get("on_hand_qty"),
            cover_days=float(r["eff_cover_left"]) if r.get("eff_cover_left") is not None else None,
            counted_at=datetime.combine(r["stock_counted_on"], datetime.min.time()) if r.get("stock_counted_on") else None,
        )
        for r in action_rows
        # Cooking Gas is entered via the gas log, not a manual count here; the
        # fresh-daily items never become a requisition, so don't dangle them
        # in the "tap to request" list either.
        if r["name"] != "Cooking Gas"
        and r["name"].strip().lower() not in _EXCLUDED_ITEM_NAMES
    ]


@router.post("/count", status_code=204)
def submit_stock_count(
    payload: StockCountCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record_stock(db, payload.ingredient_id, float(payload.qty), payload.unit, payload.unit, user.id)
    db.commit()


@router.post("/register-device", status_code=204)
def register_device(
    payload: DeviceTokenRegister,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(DeviceToken).filter(DeviceToken.token == payload.token).first()
    if existing:
        existing.user_id = user.id
        existing.platform = payload.platform
    else:
        db.add(DeviceToken(user_id=user.id, token=payload.token, platform=payload.platform))
    db.commit()
