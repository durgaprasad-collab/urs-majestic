"""Requisition approvals for the owners, inside the admin panel itself --
same data as the mobile app's Approve/Reject screen, reached with the
admin panel's usual session-cookie login instead of the mobile JWT API.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.api.routes.requisitions import notify_decision, _sync_fulfillment
from app.core.clock import business_tz
from app.core.database import get_db
from app.models.requisition import Requisition, RequisitionStatus
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["requisitions-admin"])

_WITH_USERS = (joinedload(Requisition.requested_by), joinedload(Requisition.decided_by))


def _localize(req: Requisition) -> None:
    req.created_at = req.created_at.astimezone(business_tz())
    if req.decided_at:
        req.decided_at = req.decided_at.astimezone(business_tz())


@router.get("/requisitions", response_class=HTMLResponse)
def requisitions_list(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    rows = db.query(Requisition).options(*_WITH_USERS).order_by(Requisition.created_at.desc()).all()
    if _sync_fulfillment(db, rows):
        db.commit()

    pending = [r for r in rows if r.status == RequisitionStatus.pending]
    pending.sort(key=lambda r: r.created_at)  # longest-waiting first
    history = [r for r in rows if r.status != RequisitionStatus.pending][:50]

    for r in pending + history:
        _localize(r)

    return _tmpl(request, "requisitions.html", {
        "user": user,
        "pending": pending,
        "history": history,
    })


@router.post("/requisitions/{requisition_id}/decide")
def decide_requisition(
    requisition_id: int,
    request: Request,
    decision: str = Form(...),
    decision_note: str = Form(""),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    req = db.query(Requisition).filter(Requisition.id == requisition_id).first()
    if req and req.status == RequisitionStatus.pending and decision in ("approve", "reject"):
        approved = decision == "approve"
        req.status = RequisitionStatus.approved if approved else RequisitionStatus.rejected
        req.decided_by_user_id = user.id
        req.decided_at = datetime.now(timezone.utc)
        req.decision_note = decision_note.strip() or None
        db.commit()
        notify_decision(db, req, approved)

    return RedirectResponse(url="/requisitions", status_code=303)
