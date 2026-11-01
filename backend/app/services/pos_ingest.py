"""Writing a day's Petpooja item sales: the steps shared by the manual .xlsx upload
(web/routes.py) and the add-on's automatic nightly upload (/api/live-feed/final).

The add-on reads Petpooja's Item Report rows [name, date, qty, total], which carry the
same names, quantities and totals as the "Item Report Day Wise" .xlsx, so both feed the
same pipeline. Re-running a day replaces that day's rows (load_sales), so a later, more
complete read heals an earlier one."""
import datetime
import decimal
import json
import logging
import os

from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.services.order_derived_stock import sync_order_derived_stock
from app.services.sales_stock import adjust_stock_for_pace, adjust_stock_for_sales
from scripts.import_pos import (
    build_resolver, exclude_today, load_sales, pos_alias_map, seed_menu_items,
    upsert_daily_channel_sales, write_upload_log,
)

logger = logging.getLogger("stock_upload")
_SEED = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "menu_seed.json"))
D = decimal.Decimal
MAX_ROWS = 600


def apply_item_sales(db: Session, raw_rows: list[dict], source_file: str, *, parse_errors=(),
                     declared_total=None, declared_rows=None) -> dict:
    """Rows are {raw_name, sale_date, qty, revenue}. Flushes everything; the caller commits."""
    with open(_SEED, encoding="utf-8") as f:
        seed = json.load(f)
    menu_map = seed_menu_items(db, seed)
    raw_rows, excluded_today = exclude_today(raw_rows, business_today())
    resolver = build_resolver(seed, menu_map, pos_alias_map(db))
    imported_sales, unmatched = load_sales(db, raw_rows, resolver)
    adjust_stock_for_sales(db, imported_sales)
    adjust_stock_for_pace(db, {s.sale_date for s in imported_sales})
    try:
        with db.begin_nested():
            sync_order_derived_stock(db)
    except Exception:
        # Experimental comparison model -- must never block the primary
        # (control-model) upload it's piggybacking on.
        logger.exception("order-derived deduction pass failed")
    upsert_daily_channel_sales(db, raw_rows, source_file)
    write_upload_log(db, source_file=source_file, rows=raw_rows, parse_errors=list(parse_errors),
                     excluded_today=excluded_today, declared_total=declared_total,
                     declared_rows=declared_rows, succeeded=True)
    return {"excluded_today": excluded_today, "unmatched": sorted(set(unmatched))}


def rows_from_feed(day: str, rows) -> list[dict]:
    """[[name, date, qty, total], ...] from the add-on -> import rows for one finished business day.
    Raises ValueError for anything that is not a complete, finished day."""
    try:
        sale_date = datetime.date.fromisoformat(str(day))
    except ValueError:
        raise ValueError("bad day") from None
    if sale_date >= business_today():
        raise ValueError("That business day has not finished yet")
    if not isinstance(rows, list) or not rows:
        raise ValueError("no rows")
    if len(rows) > MAX_ROWS:
        raise ValueError("too many rows")
    out = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 4:
            raise ValueError("bad row")
        name = str(r[0]).strip()
        if not name or len(name) > 300:
            raise ValueError("bad item name")
        if str(r[1])[:10] != sale_date.isoformat():
            raise ValueError(f"Row for {name[:40]!r} is dated {str(r[1])[:10]}, not {sale_date.isoformat()}")
        try:
            qty, revenue = D(str(r[2]).replace(",", "")), D(str(r[3]).replace(",", ""))
        except decimal.InvalidOperation:
            raise ValueError(f"bad quantity or total for {name[:40]!r}") from None
        if not qty.is_finite() or not revenue.is_finite() or qty < 0 or revenue < 0:
            raise ValueError(f"bad quantity or total for {name[:40]!r}")
        out.append({"raw_name": name, "sale_date": sale_date, "qty": qty, "revenue": revenue})
    return out
