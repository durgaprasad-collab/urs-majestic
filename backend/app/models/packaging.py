import decimal
from sqlalchemy import Numeric, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DishPackagingMap(Base):
    """Which container(s) a menu item uses when parceled, and how many.

    Costed at the container's real per-piece purchase price and weighted by
    the parcel rate (menu_engineering.cost_engine._parcel_rate) -- so a
    dine-in sale is never charged for a container it never used. A dish can
    have more than one row (e.g. a combo needing both a meal box and a round
    container for soup)."""

    __tablename__ = "dish_packaging_map"
    __table_args__ = (
        UniqueConstraint("menu_item_id", "ingredient_id", name="uq_dish_packaging_map"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), nullable=False, index=True
    )
    qty: Mapped[decimal.Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=decimal.Decimal("1")
    )
