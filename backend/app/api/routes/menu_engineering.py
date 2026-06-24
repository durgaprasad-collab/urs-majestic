from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.menu_engineering.analysis import get_analysis

router = APIRouter(prefix="/menu-engineering", tags=["menu-engineering"])


@router.get("/analysis", summary="Menu engineering classification (Stars / Workhorses / Puzzles / Dogs)")
def menu_engineering_analysis(db: Session = Depends(get_db)):
    return get_analysis(db)
