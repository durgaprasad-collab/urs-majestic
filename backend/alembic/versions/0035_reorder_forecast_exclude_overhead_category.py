"""Exclude ingredients in the 'Overhead' category from
v_ingredient_reorder_forecast -- these are pure financial/cost-tracking line
items (e.g. Vendor delivery charge), not physical stock. They have no shelf
quantity, no "runs out", nothing to reorder. This is distinct from
cost_role='overhead', which also covers genuinely physical, reorderable
items (Cooking Gas, Garbage bags, Apron, ...) that must keep showing up here
-- only the 'Overhead' *category* is reserved for non-physical items, and as
of this migration it has exactly one member.

Revision ID: 0035
Revises: 0034
"""

from alembic import op
import sqlalchemy as sa


revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


_NEW_VIEW = """
CREATE OR REPLACE VIEW public.v_ingredient_reorder_forecast AS
SELECT base.ingredient_id, base.name, base.category, base.cost_role,
       base.is_active, base.unit, base.buys, base.order_events,
       base.first_purchase, base.last_purchase, base.last_qty,
       base.suggested_order_qty, base.total_qty, base.avg_interval_days,
       base.daily_consumption, base.days_cover_left, base.next_order_date,
       base.runout_date, base.today, base.days_until_due, base.avg_unit_cost,
       base.est_order_cost, base.status, base.last_purchase_any,
       base.days_since_purchase, base.recently_purchased,
       ls.on_hand_qty, ls.stock_counted_on,
       CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
            THEN round(ls.on_hand_qty / base.daily_consumption, 1)
            ELSE NULL END AS stock_days_cover_left,
       CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
            THEN ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer
            ELSE NULL END AS stock_runout_date,
       CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
            THEN ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today
            ELSE NULL END AS stock_days_until_due,
       CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0 THEN
           CASE
               WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) < 0 THEN 'overdue'
               WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) = 0 THEN 'due'
               WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) <= 3 THEN 'soon'
               ELSE 'ok'
           END
           ELSE NULL END AS stock_status,
       pk.pack_size_g
  FROM (
        WITH menu_buys AS (
                 SELECT purchases.ingredient_id, purchases.qty, purchases.unit,
                        purchases.total_price, purchases.purchase_date
                   FROM purchases
                  WHERE purchases.usage_type = ANY (ARRAY['menu'::usage_type, 'others_personal'::usage_type])
                    AND purchases.deleted_at IS NULL
             ), per_unit AS (
                 SELECT menu_buys.ingredient_id, menu_buys.unit,
                        count(*) AS n, sum(menu_buys.qty) AS tq
                   FROM menu_buys
                  GROUP BY menu_buys.ingredient_id, menu_buys.unit
             ), primary_unit AS (
                 SELECT DISTINCT ON (per_unit.ingredient_id) per_unit.ingredient_id, per_unit.unit
                   FROM per_unit
                  ORDER BY per_unit.ingredient_id, per_unit.n DESC, per_unit.tq DESC
             ), buys AS (
                 SELECT b.ingredient_id, b.qty, b.unit, b.total_price, b.purchase_date
                   FROM menu_buys b
                   JOIN primary_unit u ON u.ingredient_id = b.ingredient_id AND u.unit = b.unit
             ), recent AS (
                 SELECT menu_buys.ingredient_id, max(menu_buys.purchase_date) AS last_any
                   FROM menu_buys
                  GROUP BY menu_buys.ingredient_id
             ), agg AS (
                 SELECT buys.ingredient_id, count(*) AS buys,
                        count(DISTINCT buys.purchase_date) AS order_events,
                        min(buys.purchase_date) AS first_purchase,
                        max(buys.purchase_date) AS last_purchase,
                        sum(buys.qty) AS total_qty, avg(buys.qty) AS avg_qty,
                        sum(buys.total_price) AS total_spent,
                        sum(buys.total_price) / NULLIF(sum(buys.qty), 0) AS avg_unit_cost
                   FROM buys
                  GROUP BY buys.ingredient_id
             ), last_buy AS (
                 SELECT DISTINCT ON (buys.ingredient_id) buys.ingredient_id,
                        buys.qty AS last_qty, buys.purchase_date AS last_buy_date
                   FROM buys
                  ORDER BY buys.ingredient_id, buys.purchase_date DESC, buys.qty DESC
             ), calc AS (
                 SELECT a.ingredient_id, a.buys, a.order_events, a.first_purchase,
                        a.last_purchase, a.total_qty, a.avg_qty, a.total_spent,
                        a.avg_unit_cost, u.unit AS primary_unit, lb.last_qty,
                        a.last_purchase - a.first_purchase AS span_days,
                        CASE WHEN a.order_events >= 2
                             THEN (a.last_purchase - a.first_purchase)::numeric / (a.order_events - 1)::numeric
                             ELSE NULL END AS avg_interval_days,
                        CASE WHEN a.order_events >= 2 AND (a.last_purchase - a.first_purchase) > 0
                             THEN (a.total_qty - lb.last_qty) / (a.last_purchase - a.first_purchase)::numeric
                             ELSE NULL END AS daily_consumption
                   FROM agg a
                   JOIN last_buy lb ON lb.ingredient_id = a.ingredient_id
                   JOIN primary_unit u ON u.ingredient_id = a.ingredient_id
             ), today AS (
                 SELECT (now() AT TIME ZONE 'Asia/Kolkata')::date AS d
             )
        SELECT i.id AS ingredient_id, i.name, i.category, i.cost_role, i.is_active,
               c.primary_unit AS unit, c.buys, c.order_events, c.first_purchase,
               c.last_purchase, round(c.last_qty, 2) AS last_qty,
               round(c.avg_qty, 2) AS suggested_order_qty,
               round(c.total_qty, 2) AS total_qty,
               round(c.avg_interval_days, 1) AS avg_interval_days,
               round(COALESCE(i.fixed_daily_usage_qty, c.daily_consumption), 3) AS daily_consumption,
               CASE WHEN COALESCE(i.fixed_daily_usage_qty, c.daily_consumption) > 0
                    THEN round(c.last_qty / COALESCE(i.fixed_daily_usage_qty, c.daily_consumption), 1)
                    ELSE NULL END AS days_cover_left,
               CASE WHEN c.avg_interval_days IS NOT NULL
                    THEN r.last_any + round(c.avg_interval_days)::integer
                    ELSE NULL END AS next_order_date,
               CASE WHEN COALESCE(i.fixed_daily_usage_qty, c.daily_consumption) > 0
                    THEN r.last_any + floor(c.last_qty / COALESCE(i.fixed_daily_usage_qty, c.daily_consumption))::integer
                    ELSE NULL END AS runout_date,
               t.d AS today,
               CASE WHEN c.avg_interval_days IS NOT NULL
                    THEN r.last_any + round(c.avg_interval_days)::integer - t.d
                    ELSE NULL END AS days_until_due,
               round(c.avg_unit_cost, 2) AS avg_unit_cost,
               round(c.avg_qty * c.avg_unit_cost, 2) AS est_order_cost,
               CASE
                   WHEN c.order_events < 2 AND i.fixed_daily_usage_qty IS NULL THEN 'insufficient_history'
                   WHEN (r.last_any + round(COALESCE(c.avg_interval_days, 1))::integer - t.d) < -1 THEN 'overdue'
                   WHEN (r.last_any + round(COALESCE(c.avg_interval_days, 1))::integer - t.d) <= 0 THEN 'due'
                   WHEN (r.last_any + round(COALESCE(c.avg_interval_days, 1))::integer - t.d) <= 3 THEN 'soon'
                   ELSE 'ok'
               END AS status,
               r.last_any AS last_purchase_any,
               t.d - r.last_any AS days_since_purchase,
               (t.d - r.last_any) < LEAST(3, GREATEST(1, floor(COALESCE(c.avg_interval_days, 6) / 2)::integer)) AS recently_purchased
          FROM calc c
          JOIN ingredients i ON i.id = c.ingredient_id
          JOIN recent r ON r.ingredient_id = c.ingredient_id
          CROSS JOIN today t
         WHERE i.category IS DISTINCT FROM 'Overhead'
       ) base
  LEFT JOIN (
        SELECT DISTINCT ON (ingredient_stock.ingredient_id) ingredient_stock.ingredient_id,
               ingredient_stock.on_hand_qty, ingredient_stock.counted_at::date AS stock_counted_on
          FROM ingredient_stock
         ORDER BY ingredient_stock.ingredient_id, ingredient_stock.counted_at DESC
       ) ls ON ls.ingredient_id = base.ingredient_id
  LEFT JOIN ingredients pk ON pk.id = base.ingredient_id;
"""

_OLD_VIEW = _NEW_VIEW.replace(
    "\n         WHERE i.category IS DISTINCT FROM 'Overhead'\n", "\n"
)


def upgrade() -> None:
    op.execute(sa.text(_NEW_VIEW))


def downgrade() -> None:
    op.execute(sa.text(_OLD_VIEW))
