"""Order-level CSV uploads for the Zomato and Swiggy delivery channels."""
from fastapi import APIRouter, Request, UploadFile, File, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import settings as _cfg
from app.web.deps import _tmpl, require_user
from app.models.upload_log import UploadLog
from app.services.uploads.item_matching import MenuIndex
from app.services.uploads.zomato import parse_zomato_csv, date_range_from_filename
from app.services.uploads.swiggy import parse_swiggy_csv
from app.core.clock import business_today
from app.services.uploads.channel_orders import (
    upsert_order, upsert_daily_channel_sales, write_upload_log, exclude_today_orders,
)

router = APIRouter(tags=["web"])


async def _read_csv_upload(file: UploadFile) -> bytes | None:
    """Reads the upload capped at UPLOAD_MAX_MB. Returns None if oversized."""
    max_bytes = _cfg.UPLOAD_MAX_MB * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None
    return data


def _result_ctx(user, channel_label: str, orders, days: int, unmapped, parse_errors) -> dict:
    return {
        "user": user,
        "channel": channel_label,
        "orders_count": len(orders),
        "delivered_count": sum(1 for o in orders if o.status == "delivered"),
        "cancelled_count": sum(1 for o in orders if o.status != "delivered"),
        "days_upserted": days,
        "unmapped": sorted(unmapped),
        "parse_errors": parse_errors,
    }


@router.post("/upload/zomato")
async def upload_zomato(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    if not (file.filename or "").lower().endswith(".csv"):
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": "Please upload the .csv exported from Zomato's Order history."},
            status_code=400,
        )

    data = await _read_csv_upload(file)
    if data is None:
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"File too large — maximum {_cfg.UPLOAD_MAX_MB} MB."},
            status_code=413,
        )

    try:
        menu_index = MenuIndex(db)
        orders, parse_errors, unmapped, total_rows_seen = parse_zomato_csv(data, menu_index)
        date_range = date_range_from_filename(file.filename, orders)
        orders, skipped_today = exclude_today_orders(orders, business_today())
        for order in orders:
            upsert_order(db, "zomato", order)
        days = upsert_daily_channel_sales(db, "zomato", orders, date_range, file.filename)
        write_upload_log(
            db, channel="zomato", source_file=file.filename, orders=orders,
            parse_errors=parse_errors, total_rows_seen=total_rows_seen,
            date_range=date_range, succeeded=True, rows_skipped_today=skipped_today,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        db.add(UploadLog(
            channel="zomato", source_file=file.filename or "unknown",
            rows_parsed=0, rows_inserted=0, succeeded=False, error_detail=str(exc),
        ))
        db.commit()
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"Could not process file: {exc}"},
            status_code=422,
        )

    return _tmpl(request, "upload_channel_result.html", _result_ctx(user, "Zomato", orders, days, unmapped, parse_errors))


@router.post("/upload/swiggy")
async def upload_swiggy(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    if not (file.filename or "").lower().endswith(".csv"):
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": "Please upload the .csv exported from Swiggy's past orders report."},
            status_code=400,
        )

    data = await _read_csv_upload(file)
    if data is None:
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"File too large — maximum {_cfg.UPLOAD_MAX_MB} MB."},
            status_code=413,
        )

    try:
        menu_index = MenuIndex(db)
        orders, parse_errors, unmapped, date_range, total_rows_seen = parse_swiggy_csv(data, menu_index)
        orders, skipped_today = exclude_today_orders(orders, business_today())
        for order in orders:
            upsert_order(db, "swiggy", order)
        days = upsert_daily_channel_sales(db, "swiggy", orders, date_range, file.filename)
        write_upload_log(
            db, channel="swiggy", source_file=file.filename, orders=orders,
            parse_errors=parse_errors, total_rows_seen=total_rows_seen,
            date_range=date_range, succeeded=True, rows_skipped_today=skipped_today,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        db.add(UploadLog(
            channel="swiggy", source_file=file.filename or "unknown",
            rows_parsed=0, rows_inserted=0, succeeded=False, error_detail=str(exc),
        ))
        db.commit()
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"Could not process file: {exc}"},
            status_code=422,
        )

    return _tmpl(request, "upload_channel_result.html", _result_ctx(user, "Swiggy", orders, days, unmapped, parse_errors))
