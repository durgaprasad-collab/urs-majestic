"""Petpooja order times (from the order listing upload).

Kept so stock deduction can split a sales day around a mid-day count: a
count entered on the sales day takes off only the share of that day's
sales that came after it (services/sales_stock.share_after).

Revision ID: 0050
Revises: 0049
"""
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS petpooja_order_times (
            business_date date NOT NULL,
            order_no text NOT NULL,
            created_at timestamptz NOT NULL,
            amount numeric(10, 2) NOT NULL,
            PRIMARY KEY (business_date, order_no)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS petpooja_order_times")
