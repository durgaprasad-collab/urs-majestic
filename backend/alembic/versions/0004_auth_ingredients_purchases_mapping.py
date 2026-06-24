"""Auth users, ingredients, purchases, ingredient_dish_map; derived cost columns on menu_items

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── Enums ─────────────────────────────────────────────────────────────────
    PgEnum("kg", "g", "l", "ml", "pcs", name="unit_type").create(bind, checkfirst=True)
    PgEnum("menu", "others_personal", name="usage_type").create(bind, checkfirst=True)
    PgEnum("light", "medium", "heavy", name="intensity_type").create(bind, checkfirst=True)
    PgEnum("none", "building", "reliable", name="cost_confidence_type").create(bind, checkfirst=True)

    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # ── ingredients ───────────────────────────────────────────────────────────
    op.create_table(
        "ingredients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("unit", PgEnum("kg", "g", "l", "ml", "pcs", name="unit_type", create_type=False), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_ingredients_id", "ingredients", ["id"])

    op.execute(sa.text("""
        INSERT INTO ingredients (name, unit, category, is_active) VALUES
        ('Paneer', 'kg', 'Dairy', true),
        ('Mushroom', 'kg', 'Vegetables', true),
        ('Gobi', 'kg', 'Vegetables', true),
        ('Baby Corn', 'kg', 'Vegetables', true),
        ('Onion', 'kg', 'Vegetables', true),
        ('Tomato', 'kg', 'Vegetables', true),
        ('Capsicum', 'kg', 'Vegetables', true),
        ('Oil', 'l', 'Condiments', true),
        ('Butter', 'kg', 'Dairy', true),
        ('Maida', 'kg', 'Dry Goods', true),
        ('Rice/Basmati', 'kg', 'Dry Goods', true),
        ('Wheat Flour', 'kg', 'Dry Goods', true),
        ('Ginger-Garlic', 'kg', 'Vegetables', true),
        ('Green Chilli', 'kg', 'Vegetables', true),
        ('Masala/Spices', 'kg', 'Dry Goods', true),
        ('Soya Sauce', 'ml', 'Condiments', true),
        ('Cooking Gas', 'pcs', 'Utilities', true),
        ('Packaging/Parcel', 'pcs', 'Utilities', true),
        ('Coriander', 'g', 'Vegetables', true),
        ('Curd', 'kg', 'Dairy', true)
        ON CONFLICT (name) DO NOTHING
    """))

    # ── purchases ─────────────────────────────────────────────────────────────
    op.create_table(
        "purchases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ingredient_id", sa.Integer(), sa.ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit", PgEnum("kg", "g", "l", "ml", "pcs", name="unit_type", create_type=False), nullable=False),
        sa.Column("total_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("usage_type", PgEnum("menu", "others_personal", name="usage_type", create_type=False), nullable=False),
        sa.Column("entered_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchases_id", "purchases", ["id"])
    op.create_index("ix_purchases_ingredient_id", "purchases", ["ingredient_id"])
    op.create_index("ix_purchases_purchase_date", "purchases", ["purchase_date"])

    # ── ingredient_dish_map ───────────────────────────────────────────────────
    op.create_table(
        "ingredient_dish_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ingredient_id", sa.Integer(), sa.ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("menu_item_id", sa.Integer(), sa.ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("intensity", PgEnum("light", "medium", "heavy", name="intensity_type", create_type=False), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ingredient_id", "menu_item_id", name="uq_ingredient_dish_map"),
    )
    op.create_index("ix_ingredient_dish_map_id", "ingredient_dish_map", ["id"])
    op.create_index("ix_ingredient_dish_map_ingredient_id", "ingredient_dish_map", ["ingredient_id"])
    op.create_index("ix_ingredient_dish_map_menu_item_id", "ingredient_dish_map", ["menu_item_id"])

    # ── menu_items: derived cost columns ──────────────────────────────────────
    op.add_column("menu_items", sa.Column("derived_food_cost_pct", sa.Numeric(7, 4), nullable=True))
    op.add_column("menu_items", sa.Column("derived_cost_per_unit", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "menu_items",
        sa.Column(
            "cost_confidence",
            PgEnum("none", "building", "reliable", name="cost_confidence_type", create_type=False),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("menu_items", "cost_confidence")
    op.drop_column("menu_items", "derived_cost_per_unit")
    op.drop_column("menu_items", "derived_food_cost_pct")

    op.drop_index("ix_ingredient_dish_map_menu_item_id", table_name="ingredient_dish_map")
    op.drop_index("ix_ingredient_dish_map_ingredient_id", table_name="ingredient_dish_map")
    op.drop_index("ix_ingredient_dish_map_id", table_name="ingredient_dish_map")
    op.drop_table("ingredient_dish_map")

    op.drop_index("ix_purchases_purchase_date", table_name="purchases")
    op.drop_index("ix_purchases_ingredient_id", table_name="purchases")
    op.drop_index("ix_purchases_id", table_name="purchases")
    op.drop_table("purchases")

    op.drop_index("ix_ingredients_id", table_name="ingredients")
    op.drop_table("ingredients")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    PgEnum(name="cost_confidence_type").drop(bind, checkfirst=True)
    PgEnum(name="intensity_type").drop(bind, checkfirst=True)
    PgEnum(name="usage_type").drop(bind, checkfirst=True)
    PgEnum(name="unit_type").drop(bind, checkfirst=True)
