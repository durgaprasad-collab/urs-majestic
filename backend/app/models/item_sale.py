import decimal
from datetime import date, datetime
from sqlalchemy import Text, Date, Numeric, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ItemSale(Base):
    __tablename__ = "item_sales"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    item_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    sale_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    qty: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    revenue: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
