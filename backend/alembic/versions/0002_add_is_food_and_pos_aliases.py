"""Add is_food to menu_items; create pos_aliases table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column("is_food", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.create_table(
        "pos_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "menu_item_id",
            sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pos_name", sa.String(500), nullable=False),
        sa.UniqueConstraint("pos_name", name="uq_pos_alias_pos_name"),
    )
    op.create_index("ix_pos_aliases_id", "pos_aliases", ["id"])
    op.create_index("ix_pos_aliases_menu_item_id", "pos_aliases", ["menu_item_id"])


def downgrade() -> None:
    op.drop_index("ix_pos_aliases_menu_item_id", table_name="pos_aliases")
    op.drop_index("ix_pos_aliases_id", table_name="pos_aliases")
    op.drop_table("pos_aliases")
    op.drop_column("menu_items", "is_food")
