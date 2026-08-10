import decimal
from sqlalchemy import String, Boolean, Numeric, Enum as SAEnum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    unit: Mapped[str] = mapped_column(
        SAEnum("kg", "g", "l", "ml", "pcs", name="unit_type", create_type=False),
        nullable=False,
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Cost-engine role: recipe items are priced per dish (portion x cost); overhead
    # (e.g. gas) adds a flat fixed per-dish charge; per_order (e.g. packaging) is a
    # variable per-order cost amortized across dishes sold (spend / units sold).
    cost_role: Mapped[str] = mapped_column(
        SAEnum("recipe", "overhead", "per_order", name="cost_role_type", create_type=False),
        nullable=False,
        default="recipe",
        server_default="recipe",
    )
    # Real per-portion size in grams (solids) or ml (liquids) for each intensity level.
    portion_light_g: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    portion_medium_g: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    portion_heavy_g: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Grams per piece for items bought by the piece/bunch (unit='pcs') but portioned
    # by grams — e.g. a coriander bunch ~100 g. Lets the cost engine derive rupees/gram
    # (rupees per piece / grams per piece) and v_purchase_normalised convert pcs to a
    # gram base. NULL for items already bought by weight/volume.
    pack_size_g: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Quantity increment accepted by the supplier when placing a weekly order.
    # Kept separate from pack_size_g, which means grams per physical piece.
    order_increment_qty: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)

    dish_maps: Mapped[list["IngredientDishMap"]] = relationship(
        "IngredientDishMap", back_populates="ingredient", cascade="all, delete-orphan"
    )


class IngredientDishMap(Base):
    __tablename__ = "ingredient_dish_map"
    __table_args__ = (UniqueConstraint("ingredient_id", "menu_item_id", name="uq_ingredient_dish_map"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    intensity: Mapped[str] = mapped_column(
        SAEnum("light", "medium", "heavy", name="intensity_type", create_type=False),
        nullable=False,
    )
    # Escape hatch for when an ingredient plays a genuinely different-sized role
    # in this one dish than its shared light/medium/heavy tiers assume (e.g. a
    # fried-noodle garnish on a soup vs. the same noodles as a main-course
    # portion) — set this instead of relying on `intensity`, and the cost
    # engine uses it verbatim instead of looking up the ingredient's tier.
    # NULL (the common case) means "use the intensity tier" as before.
    portion_override_g: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    ingredient: Mapped["Ingredient"] = relationship("Ingredient", back_populates="dish_maps")
