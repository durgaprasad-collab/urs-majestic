"""Shared upsert helpers for channel (Zomato/Swiggy) order imports."""
import decimal
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.order import Order, OrderItem, OrderStatus
from app.models.daily_channel_sales import DailyChannelSales


@dataclass
class ParsedItem:
    raw_name: str
    menu_item_id: int | None
    quantity: int
    unit_price: decimal.Decimal | None


@dataclass
class ParsedOrder:
    external_order_id: str
    placed_at: datetime
    status: str  # 'delivered' | 'cancelled'
    net: decimal.Decimal
    gross: decimal.Decimal
    restaurant_discount: decimal.Decimal
    platform_discount: decimal.Decimal
    items: list[ParsedItem] = field(default_factory=list)


@dataclass
class ParseError:
    line: int
    message: str


def upsert_order(db: Session, channel: str, order: ParsedOrder) -> int:
    """Upsert one order keyed on (channel, external_order_id): update
    status/total_amount/placed_at on conflict, then delete+reinsert its
    order_items (idempotent re-upload)."""
    stmt = pg_insert(Order).values(
        channel=channel,
        external_order_id=order.external_order_id,
        status=OrderStatus(order.status),
        total_amount=order.net,
        placed_at=order.placed_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Order.channel, Order.external_order_id],
        index_where=Order.external_order_id.isnot(None),
        set_={
            "status": stmt.excluded.status,
            "total_amount": stmt.excluded.total_amount,
            "placed_at": stmt.excluded.placed_at,
        },
    ).returning(Order.id)
    order_id = db.execute(stmt).scalar_one()

    db.query(OrderItem).filter(OrderItem.order_id == order_id).delete(synchronize_session=False)
    for item in order.items:
        db.add(OrderItem(
            order_id=order_id,
            menu_item_id=item.menu_item_id,
            quantity=item.quantity,
            unit_price=item.unit_price,
            raw_name=item.raw_name,
        ))
    return order_id


def upsert_daily_channel_sales(
    db: Session,
    channel: str,
    orders: list[ParsedOrder],
    date_range: tuple[date, date],
    source_file: str,
) -> int:
    """Aggregate delivered orders per business_date (from placed_at), upsert
    into daily_channel_sales, and write an explicit zero row for every date
    in `date_range` with no delivered orders. Returns dates touched."""
    totals: dict[date, dict] = {}
    d = date_range[0]
    while d <= date_range[1]:
        totals[d] = {
            "net": decimal.Decimal("0"), "orders": 0, "gross": decimal.Decimal("0"),
            "restaurant_discount": decimal.Decimal("0"), "platform_discount": decimal.Decimal("0"),
        }
        d += timedelta(days=1)

    for order in orders:
        if order.status != "delivered":
            continue
        bd = order.placed_at.date()
        bucket = totals.setdefault(bd, {
            "net": decimal.Decimal("0"), "orders": 0, "gross": decimal.Decimal("0"),
            "restaurant_discount": decimal.Decimal("0"), "platform_discount": decimal.Decimal("0"),
        })
        bucket["net"] += order.net
        bucket["orders"] += 1
        bucket["gross"] += order.gross
        bucket["restaurant_discount"] += order.restaurant_discount
        bucket["platform_discount"] += order.platform_discount

    for business_date, totals_row in totals.items():
        stmt = pg_insert(DailyChannelSales).values(
            business_date=business_date,
            channel=channel,
            net_sales=totals_row["net"],
            orders=totals_row["orders"],
            gross_order_value=totals_row["gross"],
            restaurant_discount=totals_row["restaurant_discount"],
            platform_discount=totals_row["platform_discount"],
            source_file=source_file,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["business_date", "channel"],
            set_={
                "net_sales": stmt.excluded.net_sales,
                "orders": stmt.excluded.orders,
                "gross_order_value": stmt.excluded.gross_order_value,
                "restaurant_discount": stmt.excluded.restaurant_discount,
                "platform_discount": stmt.excluded.platform_discount,
                "source_file": stmt.excluded.source_file,
                "uploaded_at": func.now(),
            },
        )
        db.execute(stmt)

    return len(totals)
