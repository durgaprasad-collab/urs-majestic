"""Record the stock timestamp used by each weekly forecast snapshot.

Revision ID: 0029
Revises: 0028
"""

from alembic import op
import sqlalchemy as sa


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "weekly_inventory_order_lines",
        sa.Column("stock_counted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("weekly_inventory_order_lines", "stock_counted_at")
