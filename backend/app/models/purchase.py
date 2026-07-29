import decimal
from datetime import date, datetime
from sqlalchemy import Numeric, Date, Text, ForeignKey, Enum as SAEnum, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    qty: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(
        SAEnum("kg", "g", "l", "ml", "pcs", name="unit_type", create_type=False),
        nullable=False,
    )
    total_price: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    usage_type: Mapped[str] = mapped_column(
        SAEnum("menu", "others_personal", "excluded_unidentified",
               name="usage_type", create_type=False),
        nullable=False,
    )
    entered_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the purchase was created from an uploaded receipt (migration 0020),
    # linking it back to the archived image + OCR text for audit.
    purchase_receipt_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_receipts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Soft delete -------------------------------------------------------
    # A purchase is a financial record. It is never removed from the table;
    # it is marked deleted so cost history stays auditable. Every view and
    # every query that feeds a cost number filters on deleted_at IS NULL.
    # The DB CHECK constraint purchases_soft_delete_complete makes a partial
    # soft delete impossible: no deletion without an actor and a reason.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    delete_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Optimistic lock ---------------------------------------------------
    # Three people enter purchases into this system concurrently. Without a
    # version check, two people editing the same row means last-write-wins
    # and one correction disappears with no error. SQLAlchemy puts this
    # column in the UPDATE ... WHERE clause and raises StaleDataError when
    # the row moved underneath the form.
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    __mapper_args__ = {"version_id_col": row_version}

    ingredient: Mapped["Ingredient"] = relationship("Ingredient")
    entered_by: Mapped["User"] = relationship("User", foreign_keys=[entered_by_user_id])
    deleted_by_user: Mapped["User"] = relationship("User", foreign_keys=[deleted_by])
