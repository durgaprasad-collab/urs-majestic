import decimal
from datetime import datetime
from sqlalchemy import Boolean, ForeignKey, Numeric, Text, DateTime, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class GasReading(Base):
    """A weighed LPG cylinder reading (gross_kg - tare_kg = kg of gas on hand).

    cylinder_role separates the in-use cylinder (actively being drawn from)
    from the spare (idle, weight doesn't change) so consumption is only
    computed against the one actually burning gas. is_new_cylinder marks a
    fresh fill/swap -- consumption is never computed across that boundary."""

    __tablename__ = "gas_readings"
    __table_args__ = (
        CheckConstraint("cylinder_role IN ('in_use', 'spare')", name="ck_gas_readings_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cylinder_role: Mapped[str] = mapped_column(Text, nullable=False)
    gross_kg: Mapped[decimal.Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    tare_kg: Mapped[decimal.Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=decimal.Decimal("20"))
    is_new_cylinder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
