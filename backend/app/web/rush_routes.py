"""Rush slips, owner test version (admin web, /rush).

Not in the staff app yet: the owner tries it on a phone in Chrome, where
printing works. Logic lives in app/services/rush.py.
"""
import json

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.services import rush, rush_scan
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["rush"])


@router.post("/rush/scan")
def scan(request: Request, photo: UploadFile = File(...), db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    limit = min(settings.UPLOAD_MAX_MB, 10) * 1024 * 1024
    data = photo.file.read(limit + 1)
    if len(data) > limit:
        return JSONResponse({"error": "Photo is too large. Crop it to the slip."}, status_code=413)
    try:
        draft = rush_scan.parse_slip(rush_scan.read_photo(data, settings.OCR_SPACE_API_KEY), rush.menu(db))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(draft)


def _owner(request: Request, db: Session):
    user, redir = require_user(request, db)
    if redir or not user.is_admin:
        return None
    return user


@router.get("/rush", response_class=HTMLResponse)
def page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        # Come back here after signing in (the Rush app opens straight on /rush).
        return RedirectResponse("/login?next=/rush", status_code=302, headers={"X-Rush-UI-Version": "1.4"})
    response = _tmpl(request, "rush.html", {
        "user": user,
        "data_json": json.dumps({"menu": rush.menu(db), "types": rush.ORDER_TYPES, "promise": rush.PROMISE_MIN}),
    })
    response.headers["X-Rush-UI-Version"] = "1.4"
    return response


@router.get("/rush/board")
def board(request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    return JSONResponse(rush.board(db))


@router.get("/rush/history")
def history(request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    raw = request.query_params.get("days", "1")
    return JSONResponse(rush.history(db, int(raw) if raw.isdigit() else 1))


@router.post("/rush/orders")
async def create(request: Request, db: Session = Depends(get_db)):
    user = _owner(request, db)
    if not user:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    body = await request.json()
    try:
        oid = rush.create_order(db, user.id, body.get("order_type"), body.get("ref"), body.get("items") or [], body.get("kot_no"))
    except (ValueError, TypeError, KeyError) as exc:
        db.rollback()
        return JSONResponse({"error": str(exc) or "Not saved"}, status_code=400)
    return JSONResponse({"id": oid, "slips": rush.board(db, only_order=oid), "board": rush.board(db)})


@router.post("/rush/items/{item_id}/{action}")
def item_ready(item_id: int, action: str, request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    if action not in ("ready", "unready"):
        return JSONResponse({"error": "Unknown action"}, status_code=400)
    rush.set_item_ready(db, item_id, action == "ready")
    return JSONResponse(rush.board(db))


@router.post("/rush/orders/{order_id}/{action}")
def status(order_id: int, action: str, request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    if action not in ("packed", "cancel"):
        return JSONResponse({"error": "Unknown action"}, status_code=400)
    rush.set_status(db, order_id, "packed" if action == "packed" else "cancelled")
    return JSONResponse(rush.board(db))
