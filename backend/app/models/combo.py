import decimal
from sqlalchemy import Text, Numeric, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ComboComponent(Base):
    """A line item inside a combo menu item: either a portion of another
    menu item's dish (component_menu_item_id + portion_factor) or a flat
    extra (fixed_cost, e.g. salad/raita) that isn't a costed menu item."""

    __tablename__ = "combo_components"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    combo_menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id"), nullable=False, index=True
    )
    component_menu_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_items.id"), nullable=True
    )
    component_label: Mapped[str] = mapped_column(Text, nullable=False)
    portion_factor: Mapped[decimal.Decimal] = mapped_column(
        Numeric, nullable=False, default=decimal.Decimal("1.0")
    )
    fixed_cost: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    is_guess: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
