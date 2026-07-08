"""Sync models to the data-reconciliation schema (upload_log,
recon_exceptions, and the v_recon_daily / v_recon_channel_status /
v_data_trust views).

Like 0007, these objects were created directly against the Supabase
database outside Alembic. Every statement here is guarded to be a no-op if
the object already exists, so this applies cleanly against the
already-migrated production DB or a fresh one.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS upload_log (
            id                    serial PRIMARY KEY,
            channel               text NOT NULL,
            source_file           text NOT NULL,
            period_start          date,
            period_end            date,
            file_declared_total   numeric(12,2),
            file_declared_rows    integer,
            rows_parsed           integer NOT NULL,
            rows_inserted         integer NOT NULL,
            rows_skipped_today    integer DEFAULT 0,
            rows_failed           integer DEFAULT 0,
            amount_inserted       numeric(12,2),
            succeeded             boolean NOT NULL,
            error_detail          text,
            uploaded_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT upload_log_channel_check
                CHECK (channel = ANY (ARRAY['petpooja','swiggy','zomato']))
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS recon_exceptions (
            id                serial PRIMARY KEY,
            business_date     date NOT NULL,
            channel           text NOT NULL,
            check_name        text NOT NULL,
            expected          numeric(12,2),
            actual            numeric(12,2),
            explanation       text NOT NULL,
            acknowledged_by   text NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_date, channel, check_name)
        )
    """))

    op.execute(sa.text("""
        CREATE OR REPLACE VIEW v_recon_daily AS
        WITH pos AS (
            SELECT item_sales.sale_date AS business_date,
                   'petpooja'::text AS channel,
                   round(sum(item_sales.revenue), 2) AS alt_sales,
                   NULL::bigint AS alt_orders
            FROM item_sales
            GROUP BY item_sales.sale_date
        ), agg AS (
            SELECT o.placed_at::date AS business_date,
                   o.channel,
                   round(sum(o.total_amount), 2) AS alt_sales,
                   count(*) AS alt_orders
            FROM orders o
            WHERE o.status = 'delivered'::orderstatus AND o.channel = ANY (ARRAY['zomato'::text, 'swiggy'::text])
            GROUP BY (o.placed_at::date), o.channel
        ), alt AS (
            SELECT pos.business_date, pos.channel, pos.alt_sales, pos.alt_orders FROM pos
            UNION ALL
            SELECT agg.business_date, agg.channel, agg.alt_sales, agg.alt_orders FROM agg
        )
        SELECT d.business_date,
               d.channel,
               d.net_sales AS channel_sales,
               COALESCE(a.alt_sales, 0::numeric) AS crosscheck_sales,
               round(d.net_sales - COALESCE(a.alt_sales, 0::numeric), 2) AS sales_diff,
               d.orders AS channel_orders,
               a.alt_orders AS crosscheck_orders,
               d.orders IS NOT NULL AND a.alt_orders IS NOT NULL AND d.orders <> a.alt_orders AS orders_mismatch,
               e.explanation,
               CASE
                   WHEN abs(d.net_sales - COALESCE(a.alt_sales, 0::numeric)) <= 0.01
                        AND NOT (d.orders IS NOT NULL AND a.alt_orders IS NOT NULL AND d.orders <> a.alt_orders)
                       THEN 'OK'
                   WHEN e.id IS NOT NULL THEN 'EXPLAINED'
                   ELSE 'MISMATCH'
               END AS status
        FROM daily_channel_sales d
        LEFT JOIN alt a ON a.business_date = d.business_date AND a.channel = d.channel
        LEFT JOIN recon_exceptions e
            ON e.business_date = d.business_date AND e.channel = d.channel
           AND e.check_name = CASE WHEN d.channel = 'petpooja' THEN 'itemsales_vs_channel' ELSE 'orders_vs_channel' END
    """))

    op.execute(sa.text("""
        CREATE OR REPLACE VIEW v_recon_channel_status AS
        SELECT channel,
               count(*) FILTER (WHERE status = 'MISMATCH') AS unexplained_mismatches,
               count(*) FILTER (WHERE status = 'EXPLAINED') AS explained_mismatches,
               max(business_date) AS latest_data_date,
               (SELECT max(d2.uploaded_at) FROM daily_channel_sales d2 WHERE d2.channel = r.channel) AS last_upload_at,
               (SELECT max(u.uploaded_at) FROM upload_log u WHERE u.channel = r.channel AND u.succeeded) AS last_logged_upload_at,
               CASE WHEN count(*) FILTER (WHERE status = 'MISMATCH') = 0 THEN 'TRUSTED' ELSE 'NEEDS ATTENTION' END AS indicator
        FROM v_recon_daily r
        GROUP BY channel
    """))

    op.execute(sa.text("""
        CREATE OR REPLACE VIEW v_data_trust AS
        SELECT
            (SELECT count(*) FROM v_recon_daily WHERE status = 'MISMATCH') AS unexplained_mismatches,
            (SELECT count(*) FROM v_recon_daily WHERE status = 'EXPLAINED') AS explained_mismatches,
            (SELECT min(business_date) FROM v_recon_daily WHERE status = 'MISMATCH') AS oldest_mismatch_date,
            CASE WHEN (SELECT count(*) FROM v_recon_daily WHERE status = 'MISMATCH') = 0
                THEN 'DATA TRUSTED' ELSE 'NEEDS ATTENTION' END AS indicator
    """))


def downgrade() -> None:
    # No-op: these objects predate this revision and hold live audit data —
    # downgrading would destroy it. Nothing to safely reverse here.
    pass
