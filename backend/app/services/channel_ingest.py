"""Writing a finished day of Zomato or Swiggy orders: the steps shared by the CSV uploads
(web/channel_upload_routes.py) and the add-on's automatic nightly upload."""
import datetime
import decimal
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import business_date_of, business_today
from app.models.daily_channel_sales import DailyChannelSales
from app.services.sales_stock import adjust_stock_for_channel
from app.services.uploads.channel_orders import (
    ParsedOrder, exclude_today_orders, upsert_daily_channel_sales, upsert_order, write_upload_log,
)

D = decimal.Decimal


def apply_channel_orders(db: Session, channel: str, day: datetime.date, orders: list[ParsedOrder], source_file: str) -> dict:
    """Flushes everything; the caller commits."""
    orders, skipped_today = exclude_today_orders(orders, business_today())
    for order in orders:
        upsert_order(db, channel, order)
    days = upsert_daily_channel_sales(db, channel, orders, (day, day), source_file)
    if not any(o.status == "delivered" for o in orders):
        # A finished day with no delivered orders is a real zero, like the CSV's in-between days
        zero = pg_insert(DailyChannelSales).values(
            business_date=day, channel=channel, net_sales=D("0"), orders=0, gross_order_value=D("0"),
            restaurant_discount=D("0"), platform_discount=D("0"), source_file=source_file)
        db.execute(zero.on_conflict_do_update(
            index_elements=["business_date", "channel"],
            set_={"net_sales": zero.excluded.net_sales, "orders": zero.excluded.orders,
                  "gross_order_value": zero.excluded.gross_order_value,
                  "restaurant_discount": zero.excluded.restaurant_discount,
                  "platform_discount": zero.excluded.platform_discount, "source_file": zero.excluded.source_file}))
        days = 1
    write_upload_log(db, channel=channel, source_file=source_file, orders=orders, parse_errors=[],
                     total_rows_seen=len(orders) + skipped_today, date_range=(day, day), succeeded=True,
                     rows_skipped_today=skipped_today)
    try:
        with db.begin_nested():
            adjust_stock_for_channel(db, channel, sorted({business_date_of(o.placed_at) for o in orders} | {day}))
    except Exception:
        # Stock estimates must never block the sales upload itself.
        logging.getLogger(__name__).exception("%s stock deduction failed", channel)
    return {"days": days, "delivered": sum(1 for o in orders if o.status == "delivered"),
            "cancelled": sum(1 for o in orders if o.status != "delivered")}
