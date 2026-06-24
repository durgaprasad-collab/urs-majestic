"""Shared helpers for web (Jinja2) routes."""
import os
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.models.user import User

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _tmpl(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    return templates.TemplateResponse(request, name, ctx or {}, status_code=status_code)


def require_user(request: Request, db: Session):
    """Return (user, None) if logged in and cleared, else (None, redirect_response)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None, RedirectResponse("/login", status_code=302)
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        request.session.clear()
        return None, RedirectResponse("/login", status_code=302)
    # Force password change before any other action
    if user.must_change_password and request.url.path != "/change-password":
        return None, RedirectResponse("/change-password?required=1", status_code=302)
    return user, None
