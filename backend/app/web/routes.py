import os
import json
import tempfile
from fastapi import APIRouter, Request, Form, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from scripts.import_pos import seed_menu_items, parse_xlsx, build_resolver, load_sales
from app.services.menu_engineering.analysis import get_analysis
from app.models.item_sale import ItemSale
from app.web.deps import _tmpl, require_user

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
        raw_rows = parse_xlsx(tmp.name)
        resolver = build_resolver(seed, menu_map)
        load_sales(db, raw_rows, resolver)
        db.commit()
    except Exception as exc:
        db.rollback()
        return _tmpl(
            request, "upload.html",
            {"user": user, "error": f"Could not process file: {exc}"},
            status_code=422,
        )
    finally:
        os.unlink(tmp.name)

    return RedirectResponse("/results", status_code=303)


@router.get("/results", response_class=HTMLResponse)
def results(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    rows = get_analysis(db)

    all_sales = db.query(ItemSale).all()
    total_rev = float(sum(s.revenue for s in all_sales))
    dates = [s.sale_date for s in all_sales]
    date_from = _fmt_date(min(dates)) if dates else "—"
    date_to = _fmt_date(max(dates)) if dates else "—"
    engine_ran = request.query_params.get("engine") == "1"

    def _top10(cls: str) -> list:
        bucket = [r for r in rows if r["classification"] == cls]
        bucket.sort(key=lambda r: r["revenue"], reverse=True)
        return bucket[:10]

    return _tmpl(request, "results.html", {
        "user": user,
        "stars": _top10("Star"),
        "workhorses": _top10("Workhorse"),
        "puzzles": _top10("Puzzle"),
        "dogs": _top10("Dog"),
        "total_rev": total_rev,
        "date_from": date_from,
        "date_to": date_to,
        "item_count": len(rows),
        "engine_ran": engine_ran,
    })
