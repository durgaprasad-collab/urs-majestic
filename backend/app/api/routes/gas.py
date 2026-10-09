"""Gas cylinders (staff app): tonight's two weigh-ins at 12:30 AM."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services import gas, push_notifications

logger = logging.getLogger("gas")
router = APIRouter(prefix="/api/gas", tags=["gas"])


class GasReadingIn(BaseModel):
    cylinder: str
    gross_kg: float
    is_new: bool = False


class GasReadingsIn(BaseModel):
    readings: list[GasReadingIn]


@router.get("/tonight")
def tonight(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return gas.tonight_payload(db)


@router.post("/readings")
def save(payload: GasReadingsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = []
    for r in payload.readings:
        if r.cylinder not in gas.LABEL:
            raise HTTPException(400, f"Unknown cylinder: {r.cylinder}")
        # Scale reading with the cylinder on it: never below empty, never above a full one + slack.
        if not (float(gas.TARE_KG) - 0.5 <= r.gross_kg <= float(gas.TARE_KG + gas.FULL_KG) + 3):
            raise HTTPException(400, f"{gas.LABEL[r.cylinder]}: {r.gross_kg} kg doesn't look like a cylinder on the scale.")
        rows.append(r.model_dump())
    if not rows:
        raise HTTPException(400, "No readings")
    summary, low = gas.save_readings(db, rows, user.id)
    db.commit()
    if low:
        owners = [uid for (uid,) in db.query(User.id).filter(User.is_admin.is_(True), User.is_active.is_(True)).all()]
        for c in low:
            try:
                push_notifications.send_to_users(
                    db, owners, title=f"{c['label']} gas cylinder almost empty",
                    body=f"{c['left']:.1f} kg left, about {c['days_left']:.1f} days at the current pace.",
                    data={"open": "gas"},
                )
            except Exception:
                logger.exception("low-gas push failed")
    return {**summary, "state": gas.tonight_payload(db)}
