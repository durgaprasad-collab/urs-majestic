"""Market prices: what delivery channels (Hyperpure, Zepto, Blinkit, ...)
charge for each ingredient, checked weekly by the Sunday price-check task.

One row per item, channel and check. price_per_unit is in the item's own unit
(per kg / litre / piece), so it compares with logged purchases.

Revision ID: 0051
Revises: 0050
"""
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS market_prices (
            id bigserial PRIMARY KEY,
            checked_on date NOT NULL,
            ingredient_id integer NOT NULL REFERENCES ingredients(id),
            channel text NOT NULL,
            product text NOT NULL,
            pack_qty numeric(12, 3) NOT NULL,
            pack_unit text NOT NULL,
            price numeric(10, 2) NOT NULL,
            price_per_unit numeric(12, 4) NOT NULL,
            item_unit text NOT NULL,
            in_stock boolean NOT NULL DEFAULT true,
            url text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_market_prices_item ON market_prices (ingredient_id, checked_on)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_prices")
