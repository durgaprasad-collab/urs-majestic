import decimal
from sqlalchemy import Numeric, Text, CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DishPackagingMap(Base):
    """Which container(s)/disposables a menu item uses, and how many.

    Costed at the item's real per-piece purchase price, weighted by
    charge_on: 'parcel' (default, weighted by menu_engineering.cost_engine
    _parcel_rate -- a container only used when the order IS a parcel, e.g.
    Round Container 300ml), 'dine_in' (weighted by 1 - _parcel_rate -- an
    item used precisely when the order is NOT a parcel, e.g. Disposable
    Plates), or 'always' (full qty every order, no fraction applied -- an
    item used regardless of parcel/dine-in, e.g. Disposable Spoons). Either
    way, a sale is never charged for packaging it never used. A dish can
    have more than one row (e.g. a combo needing both a meal box and a
    round container for soup)."""

    __tablename__ = "dish_packaging_map"
    __table_args__ = (
        UniqueConstraint("menu_item_id", "ingredient_id", name="uq_dish_packaging_map"),
        CheckConstraint("charge_on = ANY (ARRAY['parcel','dine_in','always'])", name="dish_packaging_map_charge_on_check"),
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
    charge_on: Mapped[str] = mapped_column(
        Text, nullable=False, default="parcel", server_default="parcel"
    )
