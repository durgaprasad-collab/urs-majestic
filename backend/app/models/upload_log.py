import decimal
from datetime import date, datetime
from sqlalchemy import Text, Date, Integer, Boolean, Numeric, DateTime, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class UploadLog(Base):
    """One row per upload attempt (Petpooja/Zomato/Swiggy) — audit trail
    feeding v_recon_channel_status. Silent row-dropping is forbidden, so
    every upload writes exactly one of these regardless of outcome."""

    __tablename__ = "upload_log"
    __table_args__ = (
        CheckConstraint("channel = ANY (ARRAY['petpooja','swiggy','zomato'])", name="upload_log_channel_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_declared_total: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    file_declared_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_parsed: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_skipped_today: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    rows_failed: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    amount_inserted: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
