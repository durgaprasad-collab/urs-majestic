"""Sync models to the multi-channel sales schema (daily_channel_sales,
orders.channel/external_order_id/placed_at, order_items.raw_name +
nullable menu_item_id/unit_price) and the two KPI dashboard views.

These objects were created directly against the Supabase database outside
Alembic. This revision is a guarded catch-up: every statement is written to
be a no-op if the object already exists, so it applies cleanly whether it's
hitting the already-migrated production DB or a fresh one.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── orders: channel / external_order_id / placed_at ────────────────────
    op.execute(sa.text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'direct'"))
    op.execute(sa.text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS external_order_id text"))
    op.execute(sa.text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS placed_at timestamptz"))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE orders ADD CONSTRAINT orders_channel_check
                CHECK (channel = ANY (ARRAY['direct','zomato','swiggy','counter']));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_channel_external "
        "ON orders (channel, external_order_id) WHERE external_order_id IS NOT NULL"
    ))

    # ── order_items: nullable menu_item_id/unit_price, + raw_name ──────────
    op.execute(sa.text("ALTER TABLE order_items ALTER COLUMN menu_item_id DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE order_items ALTER COLUMN unit_price DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS raw_name text"))

    # ── daily_channel_sales ─────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS daily_channel_sales (
            business_date        date NOT NULL,
            channel               text NOT NULL,
            net_sales             numeric(10,2) NOT NULL CHECK (net_sales >= 0),
            orders                integer CHECK (orders >= 0),
            gross_order_value     numeric(10,2),
            restaurant_discount   numeric(10,2) DEFAULT 0,
            platform_discount     numeric(10,2) DEFAULT 0,
            source_file           text,
            uploaded_at           timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (business_date, channel),
            CONSTRAINT daily_channel_sales_channel_check
                CHECK (channel = ANY (ARRAY['petpooja','swiggy','zomato']))
        )
    """))
    op.execute(sa.text("ALTER TABLE daily_channel_sales ENABLE ROW LEVEL SECURITY"))

    # ── dashboard views ─────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE OR REPLACE VIEW v_kpi_yesterday_sales AS
        WITH daily AS (
            SELECT business_date,
                   sum(net_sales) AS total_net_sales,
                   sum(orders) AS total_orders
            FROM daily_channel_sales
            GROUP BY business_date
        ), latest AS (
            SELECT max(business_date) AS d FROM daily
        )
        SELECT l.d AS data_through,
               cur.total_net_sales AS latest_day_sales,
               cur.total_orders AS latest_day_orders,
               prev.total_net_sales AS prior_day_sales,
               lastwk.total_net_sales AS same_weekday_last_week_sales,
               round(100.0 * (cur.total_net_sales - prev.total_net_sales) / NULLIF(prev.total_net_sales, 0::numeric), 1) AS pct_vs_prior_day,
               round(100.0 * (cur.total_net_sales - lastwk.total_net_sales) / NULLIF(lastwk.total_net_sales, 0::numeric), 1) AS pct_vs_same_weekday
        FROM latest l
        JOIN daily cur ON cur.business_date = l.d
        LEFT JOIN daily prev ON prev.business_date = (l.d - 1)
        LEFT JOIN daily lastwk ON lastwk.business_date = (l.d - 7)
    """))
    op.execute(sa.text("""
        CREATE OR REPLACE VIEW v_channel_upload_status AS
        SELECT channel,
               max(business_date) AS latest_date,
               max(max(business_date)) OVER () - max(business_date) AS days_behind_freshest,
               (max(max(business_date)) OVER () - max(business_date)) >= 2 AS is_stale
        FROM daily_channel_sales
        GROUP BY channel
    """))


def downgrade() -> None:
    # No-op: these objects predate this revision (created directly against
    # production outside Alembic) and hold live sales data — downgrading
    # would destroy it. Nothing to safely reverse here.
    pass
