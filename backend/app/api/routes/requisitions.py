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


def notify_decision(db: Session, req: Requisition, approved: bool) -> None:
    """Best-effort push to the requester once an owner decides -- shared by
    the mobile API route below and the admin-panel route in
    app/web/buy_routes.py so both decision surfaces notify the same way."""
    try:
        push_notifications.send_to_users(
            db, [req.requested_by_user_id],
            title="Requisition " + ("approved" if approved else "rejected"),
            body=req.item_name + (f" -- {req.decision_note}" if req.decision_note else ""),
            data={"requisition_id": req.id},
        )
    except Exception:
        logger.exception("Failed to notify staff of decision on requisition %s", req.id)


_OPEN = (RequisitionStatus.pending, RequisitionStatus.approved)


def _open_request_for(db: Session, name: str, ingredient_id: int | None) -> Requisition | None:
    q = db.query(Requisition).options(joinedload(Requisition.requested_by)).filter(Requisition.status.in_(_OPEN))
    if ingredient_id:
        q = q.filter((Requisition.ingredient_id == ingredient_id) | Requisition.item_name.ilike(name))
    else:
        q = q.filter(Requisition.item_name.ilike(name))
    return q.order_by(Requisition.created_at).first()


@router.get("/open")
def open_items(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """For the New Request screen: what's already requested (hidden from the
    list for everyone) and what the stock says is running out (one-tap chips)."""
    from app.services.buy_inbox import inbox

    reqs = (db.query(Requisition).options(joinedload(Requisition.requested_by))
            .filter(Requisition.status.in_(_OPEN)).order_by(Requisition.created_at).all())
    if _sync_fulfillment(db, reqs):
        db.commit()
        reqs = [r for r in reqs if r.status in _OPEN]
    requested = [{"item_name": r.item_name, "ingredient_id": r.ingredient_id, "by": r.requested_by.name,
                  "created_at": r.created_at, "status": r.status.value} for r in reqs]
    taken = {r.ingredient_id for r in reqs if r.ingredient_id}
    data = inbox(db)
    running_out = []
    for _, rows in data["groups"]:
        for i in rows:
            if i["ingredient_id"] and i["ingredient_id"] not in taken and (i["out"] or i["low"])                     and i["name"].lower() not in _EXCLUDED_ITEM_NAMES:
                running_out.append({"ingredient_id": i["ingredient_id"], "name": i["name"],
                                    "out": i["out"], "days_left": float(i["cover"]) if i["cover"] is not None else None})
    running_out.sort(key=lambda r: (not r["out"], r["days_left"] if r["days_left"] is not None else 99))
    return {"requested": requested, "running_out": running_out[:8]}


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
    # One open request per item: until it's bought or declined, nobody can
    # send it again (the app hides it; this catches two phones at once).
    existing = _open_request_for(db, name, ingredient.id if ingredient else None)
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{existing.item_name} is already requested by {existing.requested_by.name}.",
        )
    # Staff send the item name only; the owner sets the quantity on the Buy
    # screen. Quantity/unit/urgency/note from older app versions are ignored.
    req = Requisition(
        requested_by_user_id=user.id,
        item_name=name,
        ingredient_id=ingredient.id if ingredient else None,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    owner_ids = [uid for (uid,) in db.query(User.id).filter(User.is_admin.is_(True), User.is_active.is_(True)).all()]
    try:
        push_notifications.send_to_users(
            db, owner_ids,
            title=f"New request: {req.item_name}",
            body=f"{user.name} asked for it. Open Buy to set how many.",
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
    notify_decision(db, req, payload.approve)

    return db.query(Requisition).options(*_WITH_USERS).filter(Requisition.id == req.id).first()


@router.delete("/{requisition_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_requisition(requisition_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Undo: the requester takes back a request the owner hasn't decided yet."""
    req = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if req is None or req.requested_by_user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if req.status != RequisitionStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "The owner has already decided on this request.")
    db.delete(req)
    db.commit()
