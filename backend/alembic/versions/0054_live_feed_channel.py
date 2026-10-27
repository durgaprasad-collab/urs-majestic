"""Live feed: more than one channel (Petpooja walk-in, Zomato).

live_sales_reads.channel says which feed a read came from; orders_n is the
number of orders behind a Zomato read.

Revision ID: 0054
Revises: 0053
"""
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE live_sales_reads ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'petpooja'")
    op.execute("ALTER TABLE live_sales_reads ADD COLUMN IF NOT EXISTS orders_n integer")
    op.execute("CREATE INDEX IF NOT EXISTS ix_live_sales_reads_ch ON live_sales_reads (business_date, channel, read_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_live_sales_reads_ch")
    op.execute("ALTER TABLE live_sales_reads DROP COLUMN IF EXISTS orders_n")
    op.execute("ALTER TABLE live_sales_reads DROP COLUMN IF EXISTS channel")
