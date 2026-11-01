"""Swiggy's finished day, from the add-on, into the same tables the Swiggy past-orders CSV feeds.

The add-on reads Swiggy's own Past Orders list. Its fields line up with the CSV the nightly
upload parses (checked on 41 of Swiggy's own past orders, all equal): net sales are the item
GST divided by 5%, an item's unit price is its line total (packing included) over its
quantity, and anything not delivered counts as cancelled. Re-sending a day replaces it."""
import datetime
import decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import as_business_time, business_date_of, business_today
from app.models.daily_channel_sales import DailyChannelSales
from app.services.sales_stock import adjust_stock_for_channel
from app.services.uploads.channel_orders import (
    ParsedItem, ParsedOrder, exclude_today_orders, upsert_daily_channel_sales, upsert_order, write_upload_log,
)
from app.services.uploads.item_matching import MenuIndex

D = decimal.Decimal
CENT = D("0.01")
MAX_ORDERS = 400


def _money(v, what: str) -> decimal.Decimal:
    try:
        d = D(str(v if v not in (None, "") else 0))
    except decimal.InvalidOperation:
        raise ValueError(f"bad {what}") from None
    if not d.is_finite() or d < 0:
        raise ValueError(f"bad {what}")
    return d


def orders_from_feed(day: str, raw, menu_index: MenuIndex) -> tuple[list[ParsedOrder], set[str]]:
    """[{id, status, placed 'YYYY-MM-DDTHH:MM:SS', tax [sgst, cgst, igst], trade, offers,
    items [{name, qty, total}]}] for one finished calendar day -> parsed orders + unmatched item names."""
    try:
        the_day = datetime.date.fromisoformat(str(day))
    except ValueError:
        raise ValueError("bad day") from None
    if the_day >= business_today():
        raise ValueError("That day has not finished yet")
    if not isinstance(raw, list):
        raise ValueError("orders must be a list")
    if len(raw) > MAX_ORDERS:
        raise ValueError("too many orders")
    orders, unmapped = [], set()
    for o in raw:
        oid = str(o.get("id") or "").strip()
        if not oid.isdigit():
            raise ValueError(f"bad order id {oid[:20]!r}")
        try:
            placed = datetime.datetime.fromisoformat(str(o.get("placed") or ""))
        except ValueError:
            raise ValueError(f"bad time for order {oid}") from None
        if placed.date() != the_day:
            continue  # Swiggy filters by calendar day, but never trust a stray row
        tax = o.get("tax") or []
        if len(tax) != 3:
            raise ValueError(f"order {oid} has no tax figures")
        net = (sum(_money(t, "tax") for t in tax) / D("0.05")).quantize(CENT)
        discount = _money(o.get("trade"), "discount") + _money(o.get("offers"), "discount")
        items = []
        for it in o.get("items") or []:
            name, qty = str(it.get("name") or "").strip(), int(it.get("qty") or 0)
            if not name or qty <= 0:
                continue
            mid = menu_index.match(name)
            if mid is None:
                unmapped.add(name)
            items.append(ParsedItem(raw_name=name, menu_item_id=mid, quantity=qty,
                                    unit_price=(_money(it.get("total"), "item total") / qty).quantize(CENT)))
        status = "delivered" if str(o.get("status") or "").strip().lower() == "delivered" else "cancelled"
        orders.append(ParsedOrder(
            external_order_id=oid, placed_at=as_business_time(placed.replace(tzinfo=None)), status=status,
            net=net, gross=(net + discount).quantize(CENT), restaurant_discount=discount.quantize(CENT),
            platform_discount=D("0"), items=items))
    return orders, unmapped


def apply_swiggy_orders(db: Session, day: datetime.date, orders: list[ParsedOrder], source_file: str) -> dict:
    """Flushes everything; the caller commits."""
    orders, skipped_today = exclude_today_orders(orders, business_today())
    for order in orders:
        upsert_order(db, "swiggy", order)
    days = upsert_daily_channel_sales(db, "swiggy", orders, (day, day), source_file)
    if not any(o.status == "delivered" for o in orders):
        # A finished day with no delivered orders is a real zero, like the CSV's in-between days
        zero = pg_insert(DailyChannelSales).values(
            business_date=day, channel="swiggy", net_sales=D("0"), orders=0, gross_order_value=D("0"),
            restaurant_discount=D("0"), platform_discount=D("0"), source_file=source_file)
        db.execute(zero.on_conflict_do_update(
            index_elements=["business_date", "channel"],
            set_={"net_sales": zero.excluded.net_sales, "orders": zero.excluded.orders,
                  "gross_order_value": zero.excluded.gross_order_value,
                  "restaurant_discount": zero.excluded.restaurant_discount,
                  "platform_discount": zero.excluded.platform_discount, "source_file": zero.excluded.source_file}))
        days = 1
    write_upload_log(db, channel="swiggy", source_file=source_file, orders=orders, parse_errors=[],
                     total_rows_seen=len(orders) + skipped_today, date_range=(day, day), succeeded=True,
                     rows_skipped_today=skipped_today)
    try:
        with db.begin_nested():
            adjust_stock_for_channel(db, "swiggy", sorted({business_date_of(o.placed_at) for o in orders} | {day}))
    except Exception:
        # Stock estimates must never block the sales upload itself.
        import logging
        logging.getLogger(__name__).exception("swiggy stock deduction failed")
    return {"days": days, "delivered": sum(1 for o in orders if o.status == "delivered"),
            "cancelled": sum(1 for o in orders if o.status != "delivered")}
