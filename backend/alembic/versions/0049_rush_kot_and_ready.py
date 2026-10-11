"""Rush slips: Petpooja KOT number per order, food-ready time per item.

Revision ID: 0049
Revises: 0048
"""
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE kitchen_orders ADD COLUMN IF NOT EXISTS kot_no text")
    op.execute("ALTER TABLE kitchen_order_items ADD COLUMN IF NOT EXISTS ready_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE kitchen_order_items DROP COLUMN IF EXISTS ready_at")
    op.execute("ALTER TABLE kitchen_orders DROP COLUMN IF EXISTS kot_no")
