"""Live stock tab (admin web) and the endpoint the Chrome helper posts to.

The helper (live-feed-ext/) runs in the restaurant PC's Chrome, reads
Petpooja's live Item Report every ~10 minutes and POSTs the rows to
/api/live-feed with a helper code made on this page. Logic: services/live_stock.py.
"""
import datetime
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.upload_log import UploadLog
from app.services import channel_ingest, live_stock, order_listing_ingest, pos_ingest, swiggy_ingest, zomato_ingest
from app.services.uploads.item_matching import MenuIndex
from app.services.task_engine import refresh_after_upload
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


_SNAP: dict = {"at": 0.0, "body": None}
SNAP_TTL = 30  # seconds; the Live stock tab and the Today band both poll this, and the snapshot is a heavy calculation


@router.get("/live-stock/data")
def data(request: Request, db: Session = Depends(get_db)):
    if not _owner(request, db):
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    if _SNAP["body"] is None or time.monotonic() - _SNAP["at"] > SNAP_TTL:
        _SNAP["body"], _SNAP["at"] = live_stock.snapshot(db), time.monotonic()
    return JSONResponse(_SNAP["body"])


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


@router.post("/api/live-feed/final")
async def final(request: Request, db: Session = Depends(get_db)):
    """The add-on's nightly upload of a finished day: Petpooja's item report (replaces the manual
    Item Report Day Wise .xlsx), Petpooja's Order Listing (replaces the .xlsx), or Swiggy's or Zomato's orders (replace the .csv uploads). Safe to send
    again: a later read replaces that day."""
    tok = live_stock.check_token(db, request.headers.get("authorization"))
    if not tok:
        return JSONResponse({"error": "Helper code not recognised"}, status_code=401)
    try:
        body = await request.json()
        channel = body.get("channel") or "petpooja"
        if channel == "petpooja":
            rows = pos_ingest.rows_from_feed(body.get("day"), body.get("rows"))
            day, source = rows[0]["sale_date"], f"live-feed {rows[0]['sale_date'].isoformat()}"
        elif channel == "petpooja_orders":
            rows, other_day = order_listing_ingest.rows_from_feed(body.get("day"), body.get("rows"))
            day = datetime.date.fromisoformat(str(body["day"]))
            source = f"live-feed order listing {day.isoformat()}"
        elif channel in ("swiggy", "zomato"):
            parse = swiggy_ingest.orders_from_feed if channel == "swiggy" else zomato_ingest.orders_from_feed
            orders, _ = parse(body.get("day"), body.get("orders"), MenuIndex(db))
            day = datetime.date.fromisoformat(str(body["day"]))
            source = f"live-feed {channel} {day.isoformat()}"
        else:
            raise ValueError("unknown channel")
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        db.rollback()
        return JSONResponse({"error": str(exc) or "Not saved"}, status_code=400)
    try:
        if channel == "petpooja":
            result = pos_ingest.apply_item_sales(db, rows, source)
        elif channel == "petpooja_orders":
            result = order_listing_ingest.apply_order_listing(db, day, rows, source)
        else:
            result = channel_ingest.apply_channel_orders(db, channel, day, orders, source)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - reported to the add-on, which tries again later
        db.rollback()
        log.exception("nightly upload failed")
        db.add(UploadLog(channel=channel, source_file=source, rows_parsed=0, rows_inserted=0,
                         succeeded=False, error_detail=str(exc)[:500]))
        db.commit()
        return JSONResponse({"error": f"Could not save: {exc}"[:200]}, status_code=500)
    refresh_after_upload()
    if channel == "petpooja":
        log.info("nightly upload %s: %s rows", source, len(rows))
        return JSONResponse({"ok": True, "day": day.isoformat(), "rows": len(rows),
                             "qty": float(sum(r["qty"] for r in rows)), "amount": float(sum(r["revenue"] for r in rows)),
                             "unmatched": result["unmatched"][:20]})
    if channel == "petpooja_orders":
        log.info("nightly upload %s: %s orders", source, result["orders"])
        return JSONResponse({"ok": True, "day": day.isoformat(), "other_business_day": other_day, **result})
    log.info("nightly upload %s: %s delivered, %s cancelled", source, result["delivered"], result["cancelled"])
    return JSONResponse({"ok": True, "day": day.isoformat(), **result,
                         "amount": float(sum(o.net for o in orders if o.status == "delivered")),
                         "unmatched": result["unmatched"][:20]})
