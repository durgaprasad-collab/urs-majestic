"""Mobile app login: username + password -> JWT (no session cookie)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.ratelimit import _get_ip, is_rate_limited, record_failure, clear_failures
from app.core.security import verify_password, create_access_token
from app.models.user import User
from app.schemas import CurrentUserResponse, LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["mobile-auth"])

# Staff share phones sitting on a counter; a 30-day token means the app stays
# logged in like a normal mobile app instead of bouncing them to login every
# ACCESS_TOKEN_EXPIRE_MINUTES (30 min), which is right for the web admin's
# short-lived tokens but wrong for a phone in someone's apron pocket all day.
_MOBILE_TOKEN_TTL = timedelta(days=30)


@router.get("/me", response_model=CurrentUserResponse)
def me(user: User = Depends(get_current_user)):
    """Refresh the locally cached mobile session from the database.

    The staff app keeps its token for 30 days, but roles can change during that
    window. Returning the current database role prevents a stale ``is_owner``
    value in local storage from granting owner UI after a role correction.
    """
    return CurrentUserResponse(
        user_id=user.id,
        name=user.name,
        is_owner=user.is_admin,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = _get_ip(request)
    blocked, wait = is_rate_limited(ip)
    if blocked:
        mins = wait // 60 + 1
        raise HTTPException(429, f"Too many failed attempts. Try again in {mins} minute(s).")

    user = db.query(User).filter(User.username == payload.username, User.is_active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        record_failure(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    clear_failures(ip)
    token = create_access_token(subject=user.id, expires_delta=_MOBILE_TOKEN_TTL)
    return LoginResponse(
        access_token=token,
        user_id=user.id,
        name=user.name,
        is_owner=user.is_admin,
    )
