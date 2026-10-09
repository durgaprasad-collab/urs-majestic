"""Mobile stock entry: staff key in on-hand counts, focused on items the
owner's Order Forecast page says need action. Reuses the exact same
"action" bucket the web /order-forecast page computes (cadence-only
ingredients included, not just ones with a physical count) and the same
insert helper for saving a count -- this is a second front door onto the
same tables, not a parallel forecast model.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_current_user
from app.core.database import get_db
from app.models.device_token import DeviceToken
from app.models.user import User
from app.schemas import DeviceTokenRegister, LowStockItem, StockCountCreate
from app.api.routes.requisitions import _EXCLUDED_ITEM_NAMES
from app.web.reorder_routes import compute_forecast_buckets, record_stock
from app.services import stock_count

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
    # An owner correcting a number from the app is an admin edit; staff are counting.
    note = "admin_edit" if user.is_admin else "app_count"
    record_stock(db, payload.ingredient_id, float(payload.qty), payload.unit, payload.unit, user.id, note)
    db.commit()


def _num(q):
    return None if q is None else round(float(q), 3)


@router.get("/tonight")
def tonights_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Tonight's count list for the staff app: perishables + anything running
    low or bought since its last count; every stockable item on Mondays.
    `expected` is the current estimate the "Same" button fills in."""
    items = stock_count.stock_view(db)
    kind, todo = stock_count.tonights_list(items)
    s = stock_count.summary(db, items)
    return {
        "day": s["day"].isoformat(),
        "kind": kind,
        "done": s["todo_done"],
        "total": s["todo_total"],
        "last_day": s["last_day"].strftime("%a %d %b") if s["last_day"] else None,
        "last_by": s["last_by"],
        "reminder_hour": 22,
        "items": [{
            "id": i["id"], "name": i["name"], "category": "Packaging" if i["category"] == "packaging" else i["category"],
            "unit": i["unit"], "expected": _num(i["qty"]), "estimated": i["estimated"],
            "last_qty": _num(i["last_qty"]), "last_at": i["last_at"].strftime("%d %b") if i["last_at"] else None,
            "counted_tonight": i["counted_tonight"],
        } for i in todo],
        "running_low": [{"name": i["name"], "qty": _num(i["qty"]), "unit": i["unit"]}
                        for i in sorted(items, key=lambda i: i["cover"] if i["cover"] is not None else 99)
                        if i["stockable"] and i["f_low"]][:8],
    }


class DisableIn(BaseModel):
    reason: str = ""


@router.post("/items/{ingredient_id}/disable", status_code=204)
def disable_item(ingredient_id: int, payload: DisableIn, owner: User = Depends(get_current_owner),
                 db: Session = Depends(get_db)):
    """Owner-only: hide an item that is no longer used (reversible from the
    admin Stock Log's "Disabled items")."""
    if stock_count.set_active(db, ingredient_id, False, payload.reason.strip()[:200], owner.id) is None:
        raise HTTPException(404, "Item not found or already disabled")


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
