import re
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# Owner's rule (2026-10-09): every password is a 6-digit PIN, easy to type in
# the staff app. Guessing is held off by the login rate limit (5 tries per 15
# minutes per device, core/ratelimit.py). Existing longer passwords still log
# in; the rule applies when a password is set or changed.
_WEAK_PINS = {"123456", "654321", "012345", "123123", "121212"}


def check_password_strength(pw: str) -> str | None:
    """Return an error string if the PIN isn't acceptable, else None."""
    if not re.fullmatch(r"\d{6}", pw or ""):
        return "Password must be exactly 6 digits."
    if len(set(pw)) == 1 or pw in _WEAK_PINS:
        return "That PIN is too easy to guess. Pick 6 digits that aren't all the same or in order."
    return None


def create_access_token(subject: Any, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return jwt.encode(
        {"sub": str(subject), "exp": expire},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
