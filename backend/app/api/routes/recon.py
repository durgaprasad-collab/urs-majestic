from datetime import date
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.recon import get_data_trust, get_channel_status, get_daily_recon, upsert_exception

router = APIRouter(prefix="/recon", tags=["recon"])


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {
        "trust": get_data_trust(db) or {},
        "channels": get_channel_status(db),
    }


@router.get("/daily")
def daily(status: str | None = None, db: Session = Depends(get_db)):
    return get_daily_recon(db, status=status)


class ExceptionIn(BaseModel):
    business_date: date
    channel: str
    explanation: str
    acknowledged_by: str
    expected: float | None = None
    actual: float | None = None


@router.post("/exceptions")
def create_exception(payload: ExceptionIn, db: Session = Depends(get_db)):
    exc = upsert_exception(
        db,
        business_date=payload.business_date,
        channel=payload.channel,
        explanation=payload.explanation,
        acknowledged_by=payload.acknowledged_by,
        expected=payload.expected,
        actual=payload.actual,
    )
    db.commit()
    return {"id": exc.id, "business_date": exc.business_date, "channel": exc.channel, "check_name": exc.check_name}
