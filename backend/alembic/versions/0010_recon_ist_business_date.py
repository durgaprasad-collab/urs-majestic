"""Fix v_recon_daily to attribute delivery orders to the IST business date.

orders.placed_at is a timestamptz; `placed_at::date` evaluates in the DB session
timezone (UTC on Render), so an order placed after midnight IST (00:00–05:30)
was bucketed into the previous UTC day — disagreeing with daily_channel_sales
(built from the file's IST wall-clock) and producing spurious reconciliation
mismatches. Compute the business date in Asia/Kolkata instead.

CREATE OR REPLACE VIEW — additive/idempotent; safe on the live DB.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-09
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IST_DATE = "(o.placed_at AT TIME ZONE 'Asia/Kolkata')::date"
_UTC_DATE = "o.placed_at::date"


def _view(business_date_expr: str) -> str:
    return f"""
        CREATE OR REPLACE VIEW v_recon_daily AS
        WITH pos AS (
            SELECT item_sales.sale_date AS business_date,
                   'petpooja'::text AS channel,
                   round(sum(item_sales.revenue), 2) AS alt_sales,
                   NULL::bigint AS alt_orders
            FROM item_sales
            GROUP BY item_sales.sale_date
        ), agg AS (
            SELECT {business_date_expr} AS business_date,
                   o.channel,
                   round(sum(o.total_amount), 2) AS alt_sales,
                   count(*) AS alt_orders
            FROM orders o
            WHERE o.status = 'delivered'::orderstatus AND o.channel = ANY (ARRAY['zomato'::text, 'swiggy'::text])
            GROUP BY {business_date_expr}, o.channel
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
    """


def upgrade() -> None:
    op.execute(sa.text(_view(_IST_DATE)))


def downgrade() -> None:
    op.execute(sa.text(_view(_UTC_DATE)))
