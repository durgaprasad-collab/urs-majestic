"""CEO Daily Brief: a single-screen executive snapshot at /daily-brief.

The whole page context is assembled by daily_brief_v3.build_brief() in one pass
(reusing the v2 metrics + existing ceo_brief/kpi/recon analytics). This route
just gates on auth and renders.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.web.deps import _tmpl, require_user
from app.services.daily_brief_v3 import build_brief

router = APIRouter(tags=["web"])


@router.get("/daily-brief", response_class=HTMLResponse)
def daily_brief(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir

    # build_brief may return a shared cached dict — merge user into a fresh dict
    # rather than mutating it.
    ctx = build_brief(db)
    return _tmpl(request, "daily_brief.html", {**ctx, "user": user})
