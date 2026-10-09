"""Today page at /daily-brief: verdict, 21-day trend, month pace, weekday
rhythm, dishes, stock running out, and the day's generated tasks.

Data comes from services/today_page.build_today(); tasks are written by
services/task_engine after every sales upload (and on demand here).
"""
from datetime import date
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.web.deps import _tmpl, require_user
from app.services.today_page import build_today
from app.services import task_engine, shell_pulse
from app.services.daily_brief_ticket import set_google_review_count

router = APIRouter(tags=["web"])


@router.get("/daily-brief", response_class=HTMLResponse)
def daily_brief(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    raw = request.query_params.get("date", "")
    try:
        selected = date.fromisoformat(raw) if raw else None
    except ValueError:
        selected = None
    ctx = build_today(db, reporting_date=selected)
    return _tmpl(request, "today.html", {**ctx, "user": user})


@router.post("/daily-brief/tasks/{task_id}/done")
def task_done(task_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    task_engine.mark_done(db, task_id, done_by=user.name)
    return RedirectResponse("/daily-brief#tasks", status_code=303)


@router.post("/daily-brief/tasks/refresh")
def tasks_refresh(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    task_engine.generate_daily_tasks(db)
    return RedirectResponse("/daily-brief#tasks", status_code=303)


@router.post("/daily-brief/creative/google-reviews")
def set_reviews(request: Request, db: Session = Depends(get_db), count: int = Form(...)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    set_google_review_count(db, count, entered_by=user.name)
    # The review task reads this count; rebuild so it reflects the new number.
    task_engine.generate_daily_tasks(db)
    shell_pulse.invalidate()
    return RedirectResponse("/daily-brief#tasks", status_code=303)


@router.get("/prep-sheet")
def removed_prep_sheet():
    """The prep sheet was removed (owner's call, 2026-10-09); old links land on Today."""
    return RedirectResponse("/daily-brief", status_code=301)
