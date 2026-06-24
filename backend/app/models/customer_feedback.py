from datetime import datetime
from sqlalchemy import Boolean, DateTime, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class CustomerFeedback(Base):
    __tablename__ = "customer_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    review: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source: Mapped[str | None] = mapped_column(Text, nullable=True, server_default="'qr_counter'")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
