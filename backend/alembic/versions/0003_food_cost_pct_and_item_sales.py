"""Add food_cost_pct to menu_items; create item_sales table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column(
            "food_cost_pct",
            sa.Numeric(4, 3),
            nullable=False,
            server_default="0.350",
        ),
    )

    op.create_table(
        "item_sales",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("revenue", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_item_sales_id", "item_sales", ["id"])
    op.create_index("ix_item_sales_item_name", "item_sales", ["item_name"])
    op.create_index("ix_item_sales_sale_date", "item_sales", ["sale_date"])


def downgrade() -> None:
    op.drop_index("ix_item_sales_sale_date", table_name="item_sales")
    op.drop_index("ix_item_sales_item_name", table_name="item_sales")
    op.drop_index("ix_item_sales_id", table_name="item_sales")
    op.drop_table("item_sales")
    op.drop_column("menu_items", "food_cost_pct")
