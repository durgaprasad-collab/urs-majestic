"""Per-IP rate limiter for the public /api/feedback endpoint.

Separate from the login limiter: counts ALL submissions (not just failures),
window is 60 seconds, limit is FEEDBACK_RATE_LIMIT per window.
"""
import time
from collections import defaultdict
from threading import Lock

from app.core.config import settings

_lock = Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def feedback_is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    window = settings.FEEDBACK_WINDOW_SECONDS
    with _lock:
        _hits[ip] = [t for t in _hits[ip] if now - t < window]
        if len(_hits[ip]) >= settings.FEEDBACK_RATE_LIMIT:
            return True
        _hits[ip].append(now)
    return False
