import decimal
import enum
from datetime import datetime
from sqlalchemy import Integer, Text, ForeignKey, Enum as SAEnum, Numeric, DateTime, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class OrderStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    preparing = "preparing"
    ready = "ready"
    delivered = "delivered"
    cancelled = "cancelled"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "channel = ANY (ARRAY['direct','zomato','swiggy','counter'])",
            name="orders_channel_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="orderstatus", create_type=False),
        nullable=False,
        default=OrderStatus.pending,
    )
    total_amount: Mapped[decimal.Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Sales channel this order came in on ('direct','zomato','swiggy','counter' —
    # plain text + CHECK constraint in the DB, not a native enum type).
    # 'direct'/'counter' orders have no external_order_id; 'zomato'/'swiggy'
    # orders are upserted keyed on (channel, external_order_id) by the channel
    # CSV importers.
    channel: Mapped[str] = mapped_column(
        Text, nullable=False, default="direct", server_default="direct"
    )
    external_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    # Nullable: channel imports (Zomato/Swiggy) may not resolve a line item to
    # a known menu item — raw_name is always kept so it can be mapped later.
    menu_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_items.id"), nullable=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable: Zomato never provides a per-item price.
    unit_price: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    raw_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    order: Mapped["Order"] = relationship("Order", back_populates="items")
