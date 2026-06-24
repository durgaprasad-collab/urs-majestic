"""In-process IP-based login rate limiter.

Sufficient for a single-process admin tool. For multi-worker deployments,
replace the in-memory store with Redis (e.g. slowapi + Redis backend).
"""
import time
from collections import defaultdict
from threading import Lock

from app.core.config import settings

_lock = Lock()
_attempts: dict[str, list[float]] = defaultdict(list)


def _get_ip(request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_rate_limited(ip: str) -> tuple[bool, int]:
    """Return (is_blocked, seconds_remaining)."""
    now = time.monotonic()
    window = settings.LOGIN_WINDOW_SECONDS
    with _lock:
        _attempts[ip] = [t for t in _attempts[ip] if now - t < window]
        if len(_attempts[ip]) >= settings.LOGIN_MAX_ATTEMPTS:
            oldest = min(_attempts[ip])
            remaining = int(window - (now - oldest))
            return True, max(0, remaining)
    return False, 0


def record_failure(ip: str) -> None:
    with _lock:
        _attempts[ip].append(time.monotonic())


def clear_failures(ip: str) -> None:
    with _lock:
        _attempts.pop(ip, None)
