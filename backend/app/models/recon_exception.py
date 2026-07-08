import decimal
from datetime import date, datetime
from sqlalchemy import Text, Date, Numeric, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ReconException(Base):
    """An acknowledged, explained mismatch between a channel's declared
    sales and the crosscheck total (v_recon_daily). Append-only audit trail
    — the UI never deletes or edits an existing explanation."""

    __tablename__ = "recon_exceptions"
    __table_args__ = (
        UniqueConstraint("business_date", "channel", "check_name", name="recon_exceptions_business_date_channel_check_name_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    check_name: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
