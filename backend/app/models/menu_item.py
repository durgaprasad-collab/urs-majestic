import decimal
from sqlalchemy import String, Numeric, Boolean, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_food: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    food_cost_pct: Mapped[decimal.Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=decimal.Decimal("0.350")
    )
    # Populated by cost engine; NULL until first engine run with mapped data
    derived_food_cost_pct: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(7, 4), nullable=True
    )
    derived_cost_per_unit: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    cost_confidence: Mapped[str] = mapped_column(
        SAEnum("none", "building", "reliable", name="cost_confidence_type", create_type=False),
        nullable=False,
        default="none",
        server_default="none",
    )

    pos_aliases: Mapped[list["PosAlias"]] = relationship(
        "PosAlias", back_populates="menu_item", cascade="all, delete-orphan"
    )


class PosAlias(Base):
    __tablename__ = "pos_aliases"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pos_name: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    menu_item: Mapped["MenuItem"] = relationship("MenuItem", back_populates="pos_aliases")
