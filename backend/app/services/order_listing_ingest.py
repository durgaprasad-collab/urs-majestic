"""Petpooja's Order Listing for a finished day, from the add-on, through the same steps as the
manual "Order Listing" .xlsx upload (web/routes.py upload_petpooja_orders).

The add-on reads Petpooja's All Orders page, whose columns are the xlsx's (Order No., Order Type,
My Amount, Discount, Status, Created); checked on a whole day, all 77 orders equal what the xlsx
stored (order number, time and amount). Re-sending a day replaces its order times and count."""
import datetime
import decimal
import re

from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.models.upload_log import UploadLog
from app.services.uploads.petpooja_order_listing import (
    ALARM_MESSAGE, OrderListingRow, aggregate_by_day, check_aggregator_alarm, cross_check_amounts,
    find_unpaired_diffs, store_order_times, upsert_order_counts,
)

D = decimal.Decimal
MAX_ROWS = 800
_TYPE_SUFFIX = re.compile(r"\(.*\)\s*$")


def _money(v, what: str) -> decimal.Decimal:
    try:
        d = D(str(v if v not in (None, "") else 0).replace(",", "").strip("() "))
    except decimal.InvalidOperation:
        raise ValueError(f"bad {what}") from None
    if not d.is_finite() or d < 0:
        raise ValueError(f"bad {what}")
    return d


def rows_from_feed(day: str, raw) -> tuple[list[OrderListingRow], int]:
    """[{no, type, amount, discount, status, created 'YYYY-MM-DD HH:MM:SS'}] read for the business day
    -> the day's rows (business date = created minus 6 hours, as the xlsx parser has it) and how many
    rows fell on another business date (orders between 1 and 6 AM) and were left out."""
    try:
        the_day = datetime.date.fromisoformat(str(day))
    except ValueError:
        raise ValueError("bad day") from None
    if the_day >= business_today():
        raise ValueError("That business day has not finished yet")
    if not isinstance(raw, list) or not raw:
        raise ValueError("no rows")
    if len(raw) > MAX_ROWS:
        raise ValueError("too many rows")
    rows, other_day = [], 0
    for r in raw:
        order_no = str(r.get("no") or "").strip()
        if not order_no.isdigit():
            raise ValueError(f"bad order number {order_no[:20]!r}")
        try:
            created = datetime.datetime.strptime(str(r.get("created") or "").strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError(f"bad time for order {order_no}") from None
        business_date = (created - datetime.timedelta(hours=6)).date()
        if business_date != the_day:
            other_day += 1
            continue
        rows.append(OrderListingRow(
            order_no=order_no, order_type=_TYPE_SUFFIX.sub("", str(r.get("type") or "")).strip(),
            amount=_money(r.get("amount"), "amount"), discount=_money(r.get("discount"), "discount"),
            status=str(r.get("status") or "").strip(), created=created, business_date=business_date))
    return rows, other_day


def apply_order_listing(db: Session, day: datetime.date, rows: list[OrderListingRow], source_file: str) -> dict:
    """Flushes everything; the caller commits."""
    today = business_today()
    daily = aggregate_by_day(rows, today)
    upsert_order_counts(db, daily)
    store_order_times(db, rows, today)
    warnings = find_unpaired_diffs(cross_check_amounts(db, daily))
    count = sum(a["count"] for a in daily.values())
    amount = sum((a["amount"] for a in daily.values()), D("0"))
    db.add(UploadLog(
        channel="petpooja", source_file=source_file, period_start=day, period_end=day,
        file_declared_total=None, file_declared_rows=None, rows_parsed=len(rows), rows_inserted=count,
        rows_skipped_today=0, rows_failed=0, amount_inserted=amount, succeeded=True))
    db.flush()
    return {"orders": count, "amount": float(amount),
            "alarm": ALARM_MESSAGE if check_aggregator_alarm(rows) else None,
            "differs_from_item_report": [(d.isoformat(), float(x)) for d, x in warnings]}
