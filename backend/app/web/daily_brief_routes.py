"""CEO Daily Brief: a single-screen executive snapshot at /daily-brief."""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.web.deps import _tmpl, require_user
from app.services.ceo_brief import get_summary, get_menu, get_actions
from app.services.kpi import get_channel_upload_status
from app.models.upload_log import UploadLog

router = APIRouter(tags=["web"])


def _latest_failed_rows_by_channel(db: Session) -> dict[str, int]:
    """{channel: rows_failed} for each channel's most recent upload_log row,
    included only when that latest upload had rows_failed > 0."""
    out: dict[str, int] = {}
    for channel in ("petpooja", "zomato", "swiggy"):
        latest = (
            db.query(UploadLog)
            .filter(UploadLog.channel == channel)
            .order_by(UploadLog.uploaded_at.desc())
            .first()
        )
        if latest and (latest.rows_failed or 0) > 0:
            out[channel] = latest.rows_failed
    return out


@router.get("/daily-brief", response_class=HTMLResponse)
def daily_brief(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    summary = get_summary(db)
    menu_rows = get_menu(db)
    top = sorted((r for r in menu_rows if r["bucket"] == "top"), key=lambda r: r["revenue"], reverse=True)
    bottom = sorted((r for r in menu_rows if r["bucket"] == "bottom"), key=lambda r: r["revenue"])
    zero = sorted((r for r in menu_rows if r["bucket"] == "zero"), key=lambda r: r["item"])

    return _tmpl(request, "daily_brief.html", {
        "user": user,
        "summary": summary,
        "trusted": bool(summary) and summary["data_status"] == "DATA TRUSTED",
        "top": top,
        "bottom": bottom,
        "zero": zero,
        "menu_as_of": menu_rows[0]["as_of"] if menu_rows else None,
        "actions": get_actions(db),
        "channel_status": get_channel_upload_status(db),
        "failed_rows_by_channel": _latest_failed_rows_by_channel(db),
    })
