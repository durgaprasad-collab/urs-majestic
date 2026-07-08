from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.kpi import get_yesterday_sales, get_channel_upload_status

router = APIRouter(prefix="/kpi", tags=["kpi"])


@router.get("/yesterday-sales")
def yesterday_sales(db: Session = Depends(get_db)):
    return get_yesterday_sales(db) or {}


@router.get("/channel-upload-status")
def channel_upload_status(db: Session = Depends(get_db)):
    return get_channel_upload_status(db)
