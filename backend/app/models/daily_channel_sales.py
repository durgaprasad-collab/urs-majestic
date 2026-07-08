import decimal
from datetime import date, datetime
from sqlalchemy import Text, Date, Integer, Numeric, DateTime, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DailyChannelSales(Base):
    """One row per (business_date, channel): the daily revenue rollup each
    channel importer (Petpooja/Zomato/Swiggy) upserts on upload. Feeds the
    v_kpi_yesterday_sales / v_channel_upload_status dashboard views."""

    __tablename__ = "daily_channel_sales"
    __table_args__ = (
        CheckConstraint("channel = ANY (ARRAY['petpooja','swiggy','zomato'])", name="daily_channel_sales_channel_check"),
        CheckConstraint("net_sales >= 0", name="daily_channel_sales_net_sales_check"),
        CheckConstraint("orders >= 0", name="daily_channel_sales_orders_check"),
    )

    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    net_sales: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_order_value: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    restaurant_discount: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True, default=decimal.Decimal("0")
    )
    platform_discount: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True, default=decimal.Decimal("0")
    )
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
