"""Live stock tab (admin web) and the endpoint the Chrome helper posts to.

The helper (live-feed-ext/) runs in the restaurant PC's Chrome, reads
Petpooja's live Item Report every ~10 minutes and POSTs the rows to
/api/live-feed with a helper code made on this page. Logic: services/live_stock.py.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import live_stock
from app.web.deps import _tmpl, require_user

log = logging.getLogger("live_stock")
router = APIRouter(tags=["live-stock"])


def _owner(request: Request, db: Session):
    user, redir = require_user(request, db)
    return None if (redir or not user.is_admin) else user


@router.get("/live-stock", response_class=HTMLResponse)
def page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    return _tmpl(request, "live_stock.html", {"user": user, "helpers": live_stock.tokens(db)})


@router.get("/live-stock/data")
def data(request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    return JSONResponse(live_stock.snapshot(db))


@router.post("/live-stock/helper-code")
async def helper_code(request: Request, db: Session = Depends(get_db)):
    user = _owner(request, db)
    if not user:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = live_stock.new_token(db, user.id, (body or {}).get("label") or "Chrome helper")
    db.commit()
    return JSONResponse({"code": code})


@router.get("/api/live-feed/ping")
def ping(request: Request, db: Session = Depends(get_db)):
    tok = live_stock.check_token(db, request.headers.get("authorization"))
    if not tok:
        return JSONResponse({"error": "Helper code not recognised"}, status_code=401)
    db.commit()
    return JSONResponse({"ok": True, "label": tok["label"]})


@router.post("/api/live-feed")
async def feed(request: Request, db: Session = Depends(get_db)):
    tok = live_stock.check_token(db, request.headers.get("authorization"))
    if not tok:
        return JSONResponse({"error": "Helper code not recognised"}, status_code=401)
    try:
        body = await request.json()
        saved = live_stock.save_read(db, tok["id"], body.get("rows"), body.get("read_at"),
                                     channel=body.get("channel") or "petpooja", orders_raw=body.get("orders"))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        db.rollback()
        return JSONResponse({"error": str(exc) or "Not saved"}, status_code=400)
    db.commit()
    log.info("live feed (%s): %s rows, %s dishes", saved["channel"], saved["rows"], saved["dishes"])
    return JSONResponse({"ok": True, **saved})
