"""Business-timezone clock.

The server runs in UTC (Render), but the restaurant operates in IST. Any notion
of "today" that gates business logic — the same-day-export exclusion in the POS
importers, effective-date defaults — must be the restaurant's calendar day, not
the server's. Using these helpers instead of datetime.date.today() prevents a
completed IST business day from being mistaken for "today" during the
00:00–05:30 IST window when UTC is still on the previous date.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.BUSINESS_TIMEZONE)


def business_now() -> datetime:
    """Current timezone-aware datetime in the restaurant's timezone."""
    return datetime.now(business_tz())


def business_today() -> date:
    """Today's calendar date in the restaurant's timezone."""
    return business_now().date()
