import decimal
import enum
from datetime import datetime
from sqlalchemy import String, Text, Numeric, DateTime, ForeignKey, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class RequisitionUrgency(str, enum.Enum):
    normal = "normal"
    urgent = "urgent"


class RequisitionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    # Approved AND a matching purchase has since shown up in `purchases`.
    # Approved-but-not-yet-fulfilled requisitions stay visible in the app
    # instead of getting lost once the owner says yes.
    fulfilled = "fulfilled"


class Requisition(Base):
    """A staff-raised request for an item/ingredient, decided by an owner.

    Replaces the ad-hoc WhatsApp message: one row per ask, with a visible
    status and a decision trail, instead of a chat thread nobody can search.
    """
    __tablename__ = "requisitions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Best-effort match to the ingredient catalog, resolved from item_name at
    # creation time. NULL for requests that aren't a catalog ingredient (e.g.
    # "AC repair") -- those can never be cross-referenced against purchases.
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Free-form: not every requisition is a measured ingredient (e.g. "AC repair").
    quantity: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(
        SAEnum("kg", "g", "l", "ml", "pcs", name="unit_type", create_type=False),
        nullable=True,
    )
    urgency: Mapped[RequisitionUrgency] = mapped_column(
        SAEnum(RequisitionUrgency, name="requisition_urgency", create_type=False),
        nullable=False, default=RequisitionUrgency.normal, server_default="normal",
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RequisitionStatus] = mapped_column(
        SAEnum(RequisitionStatus, name="requisition_status", create_type=False),
        nullable=False, default=RequisitionStatus.pending, server_default="pending",
    )
    decided_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_purchase_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchases.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    requested_by: Mapped["User"] = relationship("User", foreign_keys=[requested_by_user_id])
    decided_by: Mapped["User"] = relationship("User", foreign_keys=[decided_by_user_id])
    ingredient: Mapped["Ingredient"] = relationship("Ingredient", foreign_keys=[ingredient_id])
