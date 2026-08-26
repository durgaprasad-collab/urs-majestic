import decimal
import enum
from datetime import date, time, datetime
from sqlalchemy import String, Text, Integer, Numeric, Date, Time, DateTime, ForeignKey, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class CateringPaymentStatus(str, enum.Enum):
    pending = "pending"
    partial = "partial"
    paid = "paid"


class CateringOrderStatus(str, enum.Enum):
    confirmed = "confirmed"
    in_prep = "in_prep"
    delivered = "delivered"
    cancelled = "cancelled"


class CateringOrder(Base):
    __tablename__ = "catering_orders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    order_taken_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_time: Mapped[time] = mapped_column(Time, nullable=False)
    # Nullable: some catering orders are picked up rather than delivered.
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    subtotal: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    total_amount: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    advance_paid: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    balance_due: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment_status: Mapped[CateringPaymentStatus] = mapped_column(
        SAEnum(CateringPaymentStatus, name="catering_payment_status", create_type=False),
        nullable=False, default=CateringPaymentStatus.pending, server_default="pending",
    )
    status: Mapped[CateringOrderStatus] = mapped_column(
        SAEnum(CateringOrderStatus, name="catering_order_status", create_type=False),
        nullable=False, default=CateringOrderStatus.confirmed, server_default="confirmed",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    items: Mapped[list["CateringOrderItem"]] = relationship(
        "CateringOrderItem", back_populates="catering_order", cascade="all, delete-orphan"
    )


class CateringOrderItem(Base):
    __tablename__ = "catering_order_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    catering_order_id: Mapped[int] = mapped_column(ForeignKey("catering_orders.id"), nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    catering_order: Mapped["CateringOrder"] = relationship("CateringOrder", back_populates="items")
