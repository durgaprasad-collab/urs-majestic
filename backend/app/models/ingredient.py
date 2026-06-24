from sqlalchemy import String, Boolean, Enum as SAEnum, ForeignKey, UniqueConstraint
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
    ingredient: Mapped["Ingredient"] = relationship("Ingredient", back_populates="dish_maps")
