"""Business Settings — the canonical financial foundation of the Restaurant OS.

Two tables:
  * fixed_expenses    — recurring costs the owner maintains (rent, salaries, …),
                        each with a frequency the engine converts to a monthly
                        equivalent. Editable from the Owner Portal, never in code.
  * business_settings — append-only history of the tunable financial assumptions
                        (desired profit, contribution margin, growth target). A
                        new value inserts a new row; previous months are never
                        overwritten.

Every downstream module (target engine, KPIs, future forecasting/AI) reads its
assumptions from here so the whole OS shares one financial model.
"""
import decimal
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Numeric, Text, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

# Frequency -> months per period. The monthly equivalent is amount / months.
FREQUENCY_MONTHS: dict[str, int] = {
    "monthly": 1,
    "quarterly": 3,
    "half_yearly": 6,
    "yearly": 12,
}

# Setting keys (the canonical tunables). Kept here so every reader agrees.
SETTING_DESIRED_PROFIT = "desired_monthly_profit"
SETTING_CONTRIBUTION_MARGIN_PCT = "contribution_margin_pct"
SETTING_GROWTH_PCT = "growth_pct"


class FixedExpense(Base):
    __tablename__ = "fixed_expenses"
    __table_args__ = (
        CheckConstraint(
            "frequency = ANY (ARRAY['monthly','quarterly','half_yearly','yearly'])",
            name="fixed_expenses_frequency_check",
        ),
        CheckConstraint("amount >= 0", name="fixed_expenses_amount_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default="'Other'")
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False, server_default="'monthly'")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @property
    def monthly_equivalent(self) -> decimal.Decimal:
        months = FREQUENCY_MONTHS.get(self.frequency, 1)
        return (self.amount / months).quantize(decimal.Decimal("0.01"))


class BusinessSetting(Base):
    """Append-only history. Current value = latest effective row for a key."""

    __tablename__ = "business_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    setting_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    value: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
