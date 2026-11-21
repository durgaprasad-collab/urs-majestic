"""Order source on Petpooja orders, and an 'ownly' sales channel.

Petpooja now receives Zomato, Swiggy and Ownly orders and tags them in the order listing's Order Type
("Delivery (Zomato)"). The order times table keeps that tag (and the discount) so a day's Petpooja item
total can be split into walk-in vs platform sales, and 'ownly' becomes a channel of its own.

Revision ID: 0060
Revises: 0059
"""
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


RECON_DAILY = """
CREATE OR REPLACE VIEW v_recon_daily AS
 WITH platform AS (
         SELECT business_date, round(sum(amount), 2) AS amount
           FROM petpooja_order_times WHERE source IS NOT NULL GROUP BY business_date
        ), pos AS (
         SELECT s.sale_date AS business_date,
            'petpooja'::text AS channel,
            round(sum(s.revenue) - COALESCE(max(p.amount), 0), 2) AS alt_sales,
            NULL::bigint AS alt_orders
           FROM item_sales s LEFT JOIN platform p ON p.business_date = s.sale_date
          GROUP BY s.sale_date
        ), agg AS (
         SELECT (o.placed_at AT TIME ZONE 'Asia/Kolkata'::text)::date AS business_date,
            o.channel,
            round(sum(o.total_amount), 2) AS alt_sales,
            count(*) AS alt_orders
           FROM orders o
          WHERE o.status = 'delivered'::orderstatus AND (o.channel = ANY (ARRAY['zomato'::text, 'swiggy'::text]))
          GROUP BY ((o.placed_at AT TIME ZONE 'Asia/Kolkata'::text)::date), o.channel
        ), own AS (
         SELECT business_date, 'ownly'::text AS channel, round(sum(amount), 2) AS alt_sales, count(*) AS alt_orders
           FROM petpooja_order_times WHERE source = 'ownly' GROUP BY business_date
        ), alt AS (
         SELECT pos.business_date, pos.channel, pos.alt_sales, pos.alt_orders FROM pos
        UNION ALL
         SELECT agg.business_date, agg.channel, agg.alt_sales, agg.alt_orders FROM agg
        UNION ALL
         SELECT own.business_date, own.channel, own.alt_sales, own.alt_orders FROM own
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
            WHEN abs(d.net_sales - COALESCE(a.alt_sales, 0::numeric)) <= 0.01 AND NOT (d.orders IS NOT NULL AND a.alt_orders IS NOT NULL AND d.orders <> a.alt_orders) THEN 'OK'::text
            WHEN e.id IS NOT NULL THEN 'EXPLAINED'::text
            ELSE 'MISMATCH'::text
        END AS status
   FROM daily_channel_sales d
     LEFT JOIN alt a ON a.business_date = d.business_date AND a.channel = d.channel
     LEFT JOIN recon_exceptions e ON e.business_date = d.business_date AND e.channel = d.channel AND e.check_name =
        CASE
            WHEN d.channel = 'petpooja'::text THEN 'itemsales_vs_channel'::text
            ELSE 'orders_vs_channel'::text
        END
"""

BRIEF_SUMMARY = """
CREATE OR REPLACE VIEW v_ceo_brief_summary AS
 WITH latest AS (
         SELECT v_brief_asof.channel_asof AS d
           FROM v_brief_asof
        )
 SELECT k.data_through,
    k.latest_day_sales,
    k.pct_vs_prior_day,
    k.pct_vs_same_weekday,
    ( SELECT sum(daily_channel_sales.net_sales) AS sum
           FROM daily_channel_sales, latest
          WHERE daily_channel_sales.business_date = latest.d AND daily_channel_sales.channel = 'petpooja'::text) AS counter_sales,
    ( SELECT sum(daily_channel_sales.net_sales) AS sum
           FROM daily_channel_sales, latest
          WHERE daily_channel_sales.business_date = latest.d AND daily_channel_sales.channel = 'zomato'::text) AS zomato_sales,
    ( SELECT sum(daily_channel_sales.net_sales) AS sum
           FROM daily_channel_sales, latest
          WHERE daily_channel_sales.business_date = latest.d AND daily_channel_sales.channel = 'swiggy'::text) AS swiggy_sales,
    ( SELECT sum(daily_channel_sales.orders) AS sum
           FROM daily_channel_sales, latest
          WHERE daily_channel_sales.business_date = latest.d AND (daily_channel_sales.channel = ANY (ARRAY['zomato'::text, 'swiggy'::text]))) AS delivery_orders,
    ( SELECT round(sum(daily_channel_sales.net_sales) / NULLIF(sum(daily_channel_sales.orders), 0)::numeric, 0) AS round
           FROM daily_channel_sales, latest
          WHERE daily_channel_sales.business_date = latest.d AND (daily_channel_sales.channel = ANY (ARRAY['zomato'::text, 'swiggy'::text]))) AS delivery_aov,
    ( SELECT round(sum(daily_channel_sales.net_sales) / NULLIF(sum(daily_channel_sales.orders), 0)::numeric, 0) AS round
           FROM daily_channel_sales, latest
          WHERE daily_channel_sales.business_date = latest.d AND daily_channel_sales.channel = 'petpooja'::text) AS counter_aov,
    t.indicator AS data_status,
    t.unexplained_mismatches,
    ( SELECT sum(daily_channel_sales.net_sales) AS sum
           FROM daily_channel_sales, latest
          WHERE daily_channel_sales.business_date = latest.d AND daily_channel_sales.channel = 'ownly'::text) AS ownly_sales
   FROM v_kpi_yesterday_sales k,
    v_data_trust t
"""


def upgrade() -> None:
    op.execute("ALTER TABLE petpooja_order_times ADD COLUMN IF NOT EXISTS source text")
    op.execute("ALTER TABLE petpooja_order_times ADD COLUMN IF NOT EXISTS discount numeric(10, 2) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE daily_channel_sales DROP CONSTRAINT IF EXISTS daily_channel_sales_channel_check")
    op.execute("""ALTER TABLE daily_channel_sales ADD CONSTRAINT daily_channel_sales_channel_check
                  CHECK (channel = ANY (ARRAY['petpooja','swiggy','zomato','catering','ownly']))""")
    op.execute(RECON_DAILY)
    op.execute(BRIEF_SUMMARY)


def downgrade() -> None:
    op.execute("DELETE FROM daily_channel_sales WHERE channel = 'ownly'")
    op.execute("ALTER TABLE daily_channel_sales DROP CONSTRAINT IF EXISTS daily_channel_sales_channel_check")
    op.execute("""ALTER TABLE daily_channel_sales ADD CONSTRAINT daily_channel_sales_channel_check
                  CHECK (channel = ANY (ARRAY['petpooja','swiggy','zomato','catering']))""")
    op.execute("ALTER TABLE petpooja_order_times DROP COLUMN IF EXISTS discount")
    op.execute("ALTER TABLE petpooja_order_times DROP COLUMN IF EXISTS source")
