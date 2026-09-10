"""Ingredient catalog for the mobile app's requisition picker -- pick from a
list instead of typing an English item name (most staff can't read English)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes.requisitions import _EXCLUDED_ITEM_NAMES
from app.core.database import get_db
from app.models.ingredient import Ingredient
from app.models.user import User
from app.schemas import IngredientRead

router = APIRouter(prefix="/api/ingredients", tags=["ingredients"])


@router.get("/", response_model=list[IngredientRead])
def list_ingredients(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Ingredient)
        .filter(Ingredient.is_active.is_(True))
        .order_by(Ingredient.category.nulls_last(), Ingredient.name)
        .all()
    )
    return [r for r in rows if r.name.strip().lower() not in _EXCLUDED_ITEM_NAMES]
