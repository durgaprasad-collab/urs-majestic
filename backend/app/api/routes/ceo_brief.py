from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.ceo_brief import get_summary, get_menu, get_actions

router = APIRouter(prefix="/ceo-brief", tags=["ceo-brief"])


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return get_summary(db) or {}


@router.get("/menu")
def menu(db: Session = Depends(get_db)):
    return get_menu(db)


@router.get("/actions")
def actions(db: Session = Depends(get_db)):
    return get_actions(db)
