"""Mobile stock entry: staff key in on-hand counts, focused on items running
low (< 3 days cover). Reuses the same cover-days math and insert helper as
the web Stock Log / Order Forecast pages -- this is a second front door onto
the same `ingredient_stock` table, not a parallel forecast model.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.device_token import DeviceToken
from app.models.user import User
from app.schemas import DeviceTokenRegister, LowStockItem, StockCountCreate
from app.api.routes.requisitions import _EXCLUDED_ITEM_NAMES
from app.web.reorder_routes import record_stock
from app.web.stock_log_routes import _stock_rows

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get("/low", response_model=list[LowStockItem])
def low_stock(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = _stock_rows(db)
    return [
        LowStockItem(
            ingredient_id=r["ingredient_id"],
            name=r["name"],
            category=r["category"],
            unit=r["unit"],
            on_hand_qty=r["cover_qty"],
            cover_days=r["cover_days"],
            counted_at=r["counted_at"],
        )
        for r in rows
        if r["cover_colour"] == "red"
        # Cooking Gas is entered via the gas log, not a manual count here; the
        # fresh-daily items never become a requisition, so don't dangle them
        # in the "tap to request" list either.
        and r["name"] != "Cooking Gas"
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
