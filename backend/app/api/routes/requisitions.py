"""Staff requisitions (mobile app): create, list, approve/reject.

Staff see their own requests; owners see everything and decide. Once
approved, an approved-and-ingredient-linked request is cross-checked against
the purchases table on every list call: if a matching purchase has landed
since the approval, the requisition flips to 'fulfilled' automatically.
Anything approved but not yet purchased just stays visible as 'approved'.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_owner, get_current_user
from app.core.database import get_db
from app.models.ingredient import Ingredient
from app.models.purchase import Purchase
from app.models.requisition import Requisition, RequisitionStatus
from app.models.user import User
from app.schemas import RequisitionCreate, RequisitionDecision, RequisitionRead
from app.services import push_notifications

logger = logging.getLogger("requisitions")

router = APIRouter(prefix="/api/requisitions", tags=["requisitions"])

_WITH_USERS = (joinedload(Requisition.requested_by), joinedload(Requisition.decided_by))

# These are bought fresh daily off-system (not tracked through purchases the
# way catalog ingredients are), so a requisition for them has nothing to be
# approved against or cross-referenced later. Exact name match, case-insensitive
# -- "Coriander powder" is a real bulk-bought spice and must NOT be caught here.
_EXCLUDED_ITEM_NAMES = {"coriander", "mint"}


def _resolve_ingredient(db: Session, item_name: str) -> Ingredient | None:
    return (
        db.query(Ingredient)
        .filter(Ingredient.name.ilike(item_name.strip()))
        .first()
    )


def _sync_fulfillment(db: Session, requisitions: list[Requisition]) -> bool:
    """Flip approved -> fulfilled where a purchase has landed since approval.
    Returns True if anything changed (caller commits)."""
    changed = False
    for req in requisitions:
        if req.status != RequisitionStatus.approved or not req.ingredient_id or not req.decided_at:
            continue
        match = (
            db.query(Purchase)
            .filter(
                Purchase.ingredient_id == req.ingredient_id,
                Purchase.deleted_at.is_(None),
                Purchase.created_at >= req.decided_at,
            )
            .order_by(Purchase.created_at.asc())
            .first()
        )
        if match:
            req.status = RequisitionStatus.fulfilled
            req.matched_purchase_id = match.id
            changed = True
    return changed


@router.post("/", response_model=RequisitionRead, status_code=status.HTTP_201_CREATED)
def create_requisition(
    payload: RequisitionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = payload.item_name.strip()
    if name.lower() in _EXCLUDED_ITEM_NAMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{name} isn't requested through the app -- it's bought fresh daily.",
        )

    ingredient = _resolve_ingredient(db, name)
    req = Requisition(
        requested_by_user_id=user.id,
        item_name=name,
        ingredient_id=ingredient.id if ingredient else None,
        quantity=payload.quantity,
        unit=payload.unit,
        urgency=payload.urgency,
        note=payload.note,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    owner_ids = [uid for (uid,) in db.query(User.id).filter(User.is_admin.is_(True), User.is_active.is_(True)).all()]
    try:
        push_notifications.send_to_users(
            db, owner_ids,
            title="New requisition",
            body=f"{user.name} requested {req.item_name}"
            + (f" ({payload.quantity} {payload.unit})" if payload.quantity else ""),
            data={"requisition_id": req.id},
        )
    except Exception:
        logger.exception("Failed to notify owners of new requisition %s", req.id)

    return db.query(Requisition).options(*_WITH_USERS).filter(Requisition.id == req.id).first()


@router.get("/", response_model=list[RequisitionRead])
def list_requisitions(
    status_filter: RequisitionStatus | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Requisition).options(*_WITH_USERS)
    if not user.is_admin:
        query = query.filter(Requisition.requested_by_user_id == user.id)
    rows = query.order_by(Requisition.created_at.desc()).all()

    if _sync_fulfillment(db, rows):
        db.commit()

    if status_filter:
        rows = [r for r in rows if r.status == status_filter]
    return rows


@router.patch("/{requisition_id}/decision", response_model=RequisitionRead)
def decide_requisition(
    requisition_id: int,
    payload: RequisitionDecision,
    owner: User = Depends(get_current_owner),
    db: Session = Depends(get_db),
):
    req = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if not req:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found")
    if req.status != RequisitionStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "Requisition already decided")

    req.status = RequisitionStatus.approved if payload.approve else RequisitionStatus.rejected
    req.decided_by_user_id = owner.id
    req.decided_at = datetime.now(timezone.utc)
    req.decision_note = payload.decision_note
    db.commit()

    try:
        push_notifications.send_to_users(
            db, [req.requested_by_user_id],
            title="Requisition " + ("approved" if payload.approve else "rejected"),
            body=req.item_name + (f" -- {payload.decision_note}" if payload.decision_note else ""),
            data={"requisition_id": req.id},
        )
    except Exception:
        logger.exception("Failed to notify staff of decision on requisition %s", req.id)

    return db.query(Requisition).options(*_WITH_USERS).filter(Requisition.id == req.id).first()
