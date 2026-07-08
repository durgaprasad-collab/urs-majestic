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


def as_business_time(dt: datetime) -> datetime:
    """Tag a naive wall-clock datetime (as parsed from a channel export, which
    is already in the restaurant's local time) with the business timezone, so it
    stores as a correct, unambiguous instant regardless of the DB session tz."""
    return dt.replace(tzinfo=business_tz()) if dt.tzinfo is None else dt


def business_date_of(dt: datetime) -> date:
    """The restaurant-local calendar date of an instant. Aware datetimes are
    converted to the business timezone first; naive ones are assumed already
    local. This is THE definition of an order's business day."""
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(business_tz()).date()
