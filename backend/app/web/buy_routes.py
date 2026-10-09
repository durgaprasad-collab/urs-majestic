"""Buy: the owners' purchasing inbox (replaces the Requisitions page).

Staff requests (item name only) and items the stock says are running out,
in one list. Approving records an approved requisition with the owner's
quantity; it closes itself when the purchase is logged.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.routes.requisitions import _sync_fulfillment, notify_decision
from app.core.clock import business_tz
from app.core.database import get_db
from app.models.ingredient import Ingredient
from app.models.requisition import Requisition, RequisitionStatus
from app.services.buy_inbox import OWNER_NOTE, bought_this_week, inbox
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["buy"])

_UNITS = {"kg", "g", "l", "ml", "pcs"}


@router.get("/buy", response_class=HTMLResponse)
def buy_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    approved = db.query(Requisition).filter(Requisition.status == RequisitionStatus.approved).all()
    if _sync_fulfillment(db, approved):
        db.commit()
    data = inbox(db)
    for r in data["waiting"]:
        r.decided_local = r.decided_at.astimezone(business_tz()) if r.decided_at else None
    return _tmpl(request, "buy.html", {"user": user, **data, "bought": bought_this_week(db)})


@router.get("/requisitions")
def old_requisitions_page():
    return RedirectResponse(url="/buy", status_code=301)


def _qty(value) -> Decimal | None:
    try:
        q = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return q if q > 0 else None


@router.post("/buy/approve")
def approve(request: Request, items: str = Form("[]"), db: Session = Depends(get_db)):
    """items = JSON [{ingredient_id, requests: [ids], qty, unit}] from the page."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    try:
        rows = json.loads(items)
    except ValueError:
        rows = []
    now = datetime.now(timezone.utc)
    notify = []
    n = 0
    for row in rows if isinstance(rows, list) else []:
        qty = _qty(row.get("qty"))
        unit = row.get("unit") if row.get("unit") in _UNITS else None
        req_ids = [int(x) for x in row.get("requests") or [] if str(x).isdigit()]
        pending = (db.query(Requisition)
                   .filter(Requisition.id.in_(req_ids), Requisition.status == RequisitionStatus.pending).all()
                   if req_ids else [])
        if pending:
            for req in pending:
                req.status = RequisitionStatus.approved
                req.quantity, req.unit = qty, unit if qty else None
                req.decided_by_user_id, req.decided_at = user.id, now
                notify.append(req)
            n += 1
            continue
        iid = row.get("ingredient_id")
        ing = db.get(Ingredient, int(iid)) if str(iid or "").isdigit() else None
        if ing is None:
            continue
        db.add(Requisition(
            requested_by_user_id=user.id, item_name=ing.name, ingredient_id=ing.id,
            quantity=qty, unit=unit if qty else None, note=OWNER_NOTE,
            status=RequisitionStatus.approved, decided_by_user_id=user.id, decided_at=now,
        ))
        n += 1
    db.commit()
    for req in notify:
        notify_decision(db, req, True)
    return RedirectResponse(url=f"/buy?approved={n}", status_code=303)


@router.post("/buy/requests/{requisition_id}/reject")
def reject(requisition_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    req = db.get(Requisition, requisition_id)
    if req and req.status == RequisitionStatus.pending:
        req.status = RequisitionStatus.rejected
        req.decided_by_user_id = user.id
        req.decided_at = datetime.now(timezone.utc)
        db.commit()
        notify_decision(db, req, False)
    return RedirectResponse(url="/buy", status_code=303)


@router.post("/buy/approved/{requisition_id}/undo")
def undo(requisition_id: int, request: Request, db: Session = Depends(get_db)):
    """Take an approval back: an owner-added line is removed, a staff request
    goes back to waiting for a decision."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    req = db.get(Requisition, requisition_id)
    if req and req.status == RequisitionStatus.approved:
        if req.note == OWNER_NOTE and req.requested_by_user_id == req.decided_by_user_id:
            db.delete(req)
        else:
            req.status = RequisitionStatus.pending
            req.decided_by_user_id = req.decided_at = None
            req.quantity = req.unit = None
        db.commit()
    return RedirectResponse(url="/buy?tab=wait", status_code=303)
