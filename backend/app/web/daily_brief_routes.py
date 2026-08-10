"""Live Daily Brief (ticket-rail): five role tickets at /daily-brief, built by
daily_brief_ticket.build_ticket_brief() — revenue strip + target line from the
existing target_engine/business_settings core, per-role metrics, and a live
Notion task list per role. Also owns the two write actions the tickets expose:
marking a task done (write-through to Notion) and the Creative panel's manual
Google review count.
"""
from urllib.parse import quote
from datetime import date
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.web.deps import _tmpl, require_user
from app.services.daily_brief_ticket import (
    build_ticket_brief, mark_task_done, set_google_review_count,
)

router = APIRouter(tags=["web"])


@router.get("/daily-brief", response_class=HTMLResponse)
def daily_brief(request: Request, db: Session = Depends(get_db), error: str | None = None):
    user, redir = require_user(request, db)
    if redir:
        return redir

    raw_date = request.query_params.get("date", "")
    try:
        selected_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        selected_date = None
    ctx = build_ticket_brief(db, reporting_date=selected_date)
    return _tmpl(request, "daily_brief.html", {**ctx, "user": user, "error": error})


@router.post("/daily-brief/tasks/{page_id}/complete")
def complete_task(page_id: str, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    ok, error = mark_task_done(db, page_id, done_by=user.name)
    if ok:
        return RedirectResponse("/daily-brief", status_code=303)
    # Surface the failure rather than silently dropping it -- the task stays
    # open in Notion, so it must stay open on the brief too.
    return RedirectResponse(f"/daily-brief?error={quote(f'Could not mark task done in Notion: {error}')}", status_code=303)


@router.post("/daily-brief/creative/google-reviews")
def set_reviews(request: Request, db: Session = Depends(get_db), count: int = Form(...)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    set_google_review_count(db, count, entered_by=user.name)
    return RedirectResponse("/daily-brief", status_code=303)
