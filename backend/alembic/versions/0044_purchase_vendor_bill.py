"""Purchases: supplier + bill number, so lines entered together are one bill.

Adds purchases.vendor and purchases.bill_ref (both optional). Existing rows
are backfilled from their notes where the supplier is recognisable:
  Hyperpure ZHPTN27-OR-0030911198 ...      -> Hyperpure / ZHPTN27-OR-0030911198
  Farmers Factory Pallikaranai SO-55158 ... -> Farmers Factory / SO-55158
  Galaxy (Koyambedu) bill 376 ...           -> Galaxy (Koyambedu) / 376
  Zomato instant grocery ORD63213290827 ... -> Zomato instant grocery / ORD...
  ... cash memo ...                         -> Cash memo
Rows with nothing recognisable stay blank (shown as "Local purchases").

Revision ID: 0044
Revises: 0043
"""
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS vendor text")
    op.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS bill_ref text")
    op.execute("CREATE INDEX IF NOT EXISTS ix_purchases_date_vendor ON purchases (purchase_date, vendor)")
    # Backfill only blank rows; updating vendor/bill_ref doesn't fire the stock
    # or ledger triggers (they watch qty/unit/ingredient/date/usage/deleted).
    op.execute("""
        UPDATE purchases SET vendor = 'Hyperpure', bill_ref = substring(notes from '(ZHP[A-Z0-9]+-OR-[0-9]+)')
         WHERE vendor IS NULL AND notes ~ 'Hyperpure'
    """)
    op.execute("""
        UPDATE purchases SET vendor = 'Farmers Factory', bill_ref = substring(notes from '(SO-[0-9]+)')
         WHERE vendor IS NULL AND notes ~* 'Farmers Factory'
    """)
    op.execute("""
        UPDATE purchases SET vendor = 'Galaxy (Koyambedu)', bill_ref = substring(notes from 'bill ([0-9]+)')
         WHERE vendor IS NULL AND notes ~* 'Galaxy'
    """)
    op.execute("""
        UPDATE purchases SET vendor = 'Zomato instant grocery', bill_ref = substring(notes from '(ORD[0-9]+)')
         WHERE vendor IS NULL AND notes ~* 'Zomato instant'
    """)
    op.execute("UPDATE purchases SET vendor = 'Cash memo' WHERE vendor IS NULL AND notes ~* 'cash memo'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_purchases_date_vendor")
    op.execute("ALTER TABLE purchases DROP COLUMN IF EXISTS bill_ref")
    op.execute("ALTER TABLE purchases DROP COLUMN IF EXISTS vendor")
