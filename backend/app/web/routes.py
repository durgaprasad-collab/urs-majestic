import os
import json
import logging
import tempfile
import decimal
from fastapi import APIRouter, Request, Form, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.clock import business_today
from datetime import date
from app.services.task_engine import refresh_after_upload
from scripts.import_pos import (
    seed_menu_items, parse_xlsx, build_resolver, load_sales, pos_alias_map,
    exclude_today, upsert_daily_channel_sales, write_upload_log,
)
from app.services import menu_page
from app.services.uploads.petpooja_order_listing import (
    parse_order_listing_xlsx, aggregate_by_day, check_aggregator_alarm,
    find_unpaired_diffs, upsert_order_counts, cross_check_amounts, ALARM_MESSAGE,
)
from app.models.item_sale import ItemSale
from app.models.upload_log import UploadLog
from app.services.sales_stock import adjust_stock_for_sales
from app.services.order_derived_stock import sync_order_derived_stock
from app.web.deps import _tmpl, require_user

logger = logging.getLogger("stock_upload")

_SEED = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "menu_seed.json")
)

router = APIRouter(tags=["web"])


def _fmt_date(d) -> str:
    return f"{d.day} {d.strftime('%b')} {d.year}"


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/upload", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    # Legacy alias — dashboard.html was a strictly smaller version of the
    # Today page. Redirect rather than maintain two near-duplicate templates.
    user, redir = require_user(request, db)
    if redir:
        return redir
    return RedirectResponse("/daily-brief", status_code=302)


@router.get("/upload", response_class=HTMLResponse)
def upload_get(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    return _tmpl(request, "upload.html", {"user": user, "error": None})


@router.post("/upload")
async def upload_post(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    if not (file.filename or "").lower().endswith(".xlsx"):
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": "Please upload an .xlsx file exported from your POS system."},
            status_code=400,
        )

    # Read with size cap — reject anything larger than UPLOAD_MAX_MB
    from app.core.config import settings as _cfg
    _max_bytes = _cfg.UPLOAD_MAX_MB * 1024 * 1024
    data = await file.read(_max_bytes + 1)
    if len(data) > _max_bytes:
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"File too large — maximum {_cfg.UPLOAD_MAX_MB} MB."},
            status_code=413,
        )
    # Validate XLSX magic bytes (ZIP PK header)
    if data[:4] != b"\x50\x4b\x03\x04":
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": "File does not appear to be a valid .xlsx file."},
            status_code=400,
        )

    # Windows requires delete=False — file must be closed before openpyxl can open it
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    try:
        tmp.write(data)
        tmp.close()

        with open(_SEED, encoding="utf-8") as f:
            seed = json.load(f)

        menu_map = seed_menu_items(db, seed)
        raw_rows, parse_errors, declared_total, declared_rows = parse_xlsx(tmp.name)
        raw_rows, excluded_today = exclude_today(raw_rows, business_today())
        resolver = build_resolver(seed, menu_map, pos_alias_map(db))
        imported_sales, _unmatched = load_sales(db, raw_rows, resolver)
        adjust_stock_for_sales(db, imported_sales)
        try:
            with db.begin_nested():
                sync_order_derived_stock(db)
        except Exception:
            # Experimental comparison model -- must never block the primary
            # (control-model) upload it's piggybacking on.
            logger.exception("order-derived deduction pass failed")
        upsert_daily_channel_sales(db, raw_rows, file.filename)
        write_upload_log(
            db,
            source_file=file.filename,
            rows=raw_rows,
            parse_errors=parse_errors,
            excluded_today=excluded_today,
            declared_total=declared_total,
            declared_rows=declared_rows,
            succeeded=True,
        )
        db.commit()
        refresh_after_upload()
    except Exception as exc:
        db.rollback()
        db.add(UploadLog(
            channel="petpooja",
            source_file=file.filename or "unknown",
            rows_parsed=0,
            rows_inserted=0,
            succeeded=False,
            error_detail=str(exc),
        ))
        db.commit()
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"Could not process file: {exc}"},
            status_code=422,
        )
    finally:
        os.unlink(tmp.name)

    params = []
    if excluded_today:
        params.append(f"today_excluded={excluded_today}")
    if parse_errors:
        lines = ",".join(str(e.line) for e in parse_errors[:20])
        params.append(f"parse_errors={len(parse_errors)}&parse_error_lines={lines}")
    redirect_url = "/results" + ("?" + "&".join(params) if params else "")
    return RedirectResponse(redirect_url, status_code=303)


@router.post("/upload/petpooja-orders")
async def upload_petpooja_orders(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Petpooja 'Order Listing' export — recovers per-day COUNTER order
    counts (and AOV, via v_ceo_brief_summary) for daily_channel_sales.
    Never touches net_sales — that's owned by the item-report upload."""
    user, redir = require_user(request, db)
    if redir:
        return redir

    if not (file.filename or "").lower().endswith(".xlsx"):
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": "Please upload the .xlsx Order Listing export from Petpooja Reports."},
            status_code=400,
        )

    from app.core.config import settings as _cfg
    _max_bytes = _cfg.UPLOAD_MAX_MB * 1024 * 1024
    data = await file.read(_max_bytes + 1)
    if len(data) > _max_bytes:
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"File too large — maximum {_cfg.UPLOAD_MAX_MB} MB."},
            status_code=413,
        )
    if data[:4] != b"\x50\x4b\x03\x04":
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": "File does not appear to be a valid .xlsx file."},
            status_code=400,
        )

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    try:
        tmp.write(data)
        tmp.close()

        today = business_today()
        rows, parse_errors = parse_order_listing_xlsx(tmp.name)
        alarm = check_aggregator_alarm(rows)
        skipped_today = sum(1 for r in rows if r.status != "Cancelled" and r.business_date >= today)

        daily = aggregate_by_day(rows, today)
        dates_updated, dates_skipped_no_row = upsert_order_counts(db, daily)
        diffs = cross_check_amounts(db, daily)
        warnings = find_unpaired_diffs(diffs)

        rows_inserted = sum(agg["count"] for agg in daily.values())
        amount_inserted = sum((agg["amount"] for agg in daily.values()), decimal.Decimal("0"))
        dates = list(daily.keys())
        log = UploadLog(
            channel="petpooja",
            source_file=file.filename,
            period_start=min(dates) if dates else None,
            period_end=max(dates) if dates else None,
            file_declared_total=None,
            file_declared_rows=None,
            rows_parsed=len(rows),
            rows_inserted=rows_inserted,
            rows_skipped_today=skipped_today,
            rows_failed=len(parse_errors),
            amount_inserted=amount_inserted,
            succeeded=True,
        )
        db.add(log)
        db.commit()
    except Exception as exc:
        db.rollback()
        db.add(UploadLog(
            channel="petpooja",
            source_file=file.filename or "unknown",
            rows_parsed=0,
            rows_inserted=0,
            succeeded=False,
            error_detail=str(exc),
        ))
        db.commit()
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"Could not process file: {exc}"},
            status_code=422,
        )
    finally:
        os.unlink(tmp.name)

    return _tmpl(request, "upload_order_listing_result.html", {
        "user": user,
        "dates_updated": dates_updated,
        "dates_skipped_no_row": sorted(dates_skipped_no_row),
        "skipped_today": skipped_today,
        "warnings": warnings,
        "alarm": alarm,
        "alarm_message": ALARM_MESSAGE,
        "parse_errors": parse_errors,
    })


@router.get("/results", response_class=HTMLResponse)
def results(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    raw = request.query_params.get("days", "")
    data = menu_page.page(db, int(raw) if raw.isdigit() else 30)
    return _tmpl(request, "results.html", {
        "user": user, **data,
        "data_json": json.dumps({k: data[k] for k in ("items", "median_q", "median_cpu", "days")} if data.get("items") else {"items": []}),
        "notice": request.query_params.get("notice"),
        "today_excluded": request.query_params.get("today_excluded"),
        "parse_errors_count": request.query_params.get("parse_errors"),
        "parse_error_lines": request.query_params.get("parse_error_lines"),
    })


@router.post("/results/link")
async def results_link(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    body = await request.json()
    pos_name = (body.get("pos_name") or "").strip()
    try:
        mid = int(body.get("menu_item_id"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Pick a menu item"}, status_code=400)
    if not pos_name:
        return JSONResponse({"error": "Missing name"}, status_code=400)
    try:
        n = menu_page.link(db, pos_name, mid)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    logger.info("menu link by %s: %r -> %s (%s rows)", user.username, pos_name, mid, n)
    return JSONResponse({"ok": True, "rows": n})
