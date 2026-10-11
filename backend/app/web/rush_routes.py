"""Rush slips, owner test version (admin web, /rush).

Not in the staff app yet: the owner tries it on a phone in Chrome, where
printing works. Logic lives in app/services/rush.py.
"""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import rush
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["rush"])


def _owner(request: Request, db: Session):
    user, redir = require_user(request, db)
    if redir or not user.is_admin:
        return None
    return user


@router.get("/rush", response_class=HTMLResponse)
def page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    return _tmpl(request, "rush.html", {
        "user": user,
        "data_json": json.dumps({"menu": rush.menu(db), "types": rush.ORDER_TYPES, "promise": rush.PROMISE_MIN}),
    })


@router.get("/rush/board")
def board(request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    return JSONResponse(rush.board(db))


@router.post("/rush/orders")
async def create(request: Request, db: Session = Depends(get_db)):
    user = _owner(request, db)
    if not user:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    body = await request.json()
    try:
        oid = rush.create_order(db, user.id, body.get("order_type"), body.get("ref"), body.get("items") or [])
    except (ValueError, TypeError, KeyError) as exc:
        db.rollback()
        return JSONResponse({"error": str(exc) or "Not saved"}, status_code=400)
    return JSONResponse({"id": oid, "slips": rush.board(db, only_order=oid), "board": rush.board(db)})


@router.post("/rush/orders/{order_id}/{action}")
def status(order_id: int, action: str, request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    if action not in ("packed", "cancel"):
        return JSONResponse({"error": "Unknown action"}, status_code=400)
    rush.set_status(db, order_id, "packed" if action == "packed" else "cancelled")
    return JSONResponse(rush.board(db))
