"""Extend the reorder forecast to cover others_personal items, and add an
explicit fixed-daily-usage override for items whose consumption doesn't
follow purchase cadence at all (e.g. one garbage bag every day, regardless
of when bags were bought).

Two changes, both additive:

1. ingredients.fixed_daily_usage_qty (nullable numeric) -- a new, explicit
   column, not an overload of any existing one. NULL for every ingredient
   except those the owner has confirmed a fixed daily rate for. When set,
   the reorder forecast uses this value directly instead of computing
   daily_consumption from purchase history -- same override pattern already
   used for on-hand stock counts (ingredient_stock) and Cooking Gas
   (gas_readings), just persisted on the ingredient itself since a fixed
   rate is a property of the ingredient, not a point-in-time observation.

2. v_ingredient_reorder_forecast's menu_buys CTE was filtered to
   `usage_type = 'menu'` only (see 0012), which meant every
   others_personal-tagged ingredient was invisible to the reorder forecast
   AND the Stock Log page (both read this view's daily_consumption/
   cover_days) no matter how much real purchase history it had. Broadening
   the filter to `usage_type IN ('menu', 'others_personal')` lets the exact
   same, already-proven cadence math (avg purchase interval, avg qty) apply
   to non-recipe operational supplies too -- no separate implementation
   needed. This does NOT touch cost_engine.py or any COGS calculation,
   which reads `purchases.usage_type = 'menu'` directly and is untouched.

Preserves history: no rows deleted, no existing column repurposed. The view
change is purely additive (0019's own note: "Only additive -> CREATE OR
REPLACE is happy") -- every existing 'menu' ingredient's numbers are
unchanged; only newly-included others_personal rows get new behavior, and
only ingredients with fixed_daily_usage_qty set get an overridden
daily_consumption.

Revision ID: 0031
Revises: 0030
"""

from alembic import op
import sqlalchemy as sa


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


_ADD_COLUMN = """
ALTER TABLE public.ingredients
    ADD COLUMN IF NOT EXISTS fixed_daily_usage_qty numeric NULL;

COMMENT ON COLUMN public.ingredients.fixed_daily_usage_qty IS
  'Explicit fixed daily consumption rate, in the ingredient''s forecast (primary) unit. NULL for almost everything -- only set when the owner has confirmed usage does not track purchase cadence at all (e.g. 1 garbage bag/day). When set, v_ingredient_reorder_forecast uses this instead of computing daily_consumption from purchase history.';
"""

_BACKFILL = """
UPDATE public.ingredients
   SET fixed_daily_usage_qty = 1
 WHERE name = 'Garbage bag (large)'
   AND fixed_daily_usage_qty IS DISTINCT FROM 1;
"""

# Identical to 0019's view body except: (1) menu_buys now includes
# usage_type='others_personal' alongside 'menu', renamed conceptually but
# kept as `menu_buys` to minimize diff noise -- see module docstring; (2)
# daily_consumption/days_cover_left/runout_date/days_until_due now read
# COALESCE(i.fixed_daily_usage_qty, c.daily_consumption) instead of the bare
# cadence-derived value.
_VIEW = """
CREATE OR REPLACE VIEW public.v_ingredient_reorder_forecast AS
SELECT base.*,
    ls.on_hand_qty,
    ls.stock_counted_on,
    CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
         THEN round(ls.on_hand_qty / base.daily_consumption, 1) END AS stock_days_cover_left,
    CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
         THEN ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer END AS stock_runout_date,
    CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
         THEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) END AS stock_days_until_due,
    CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0 THEN
        CASE
            WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) < 0 THEN 'overdue'::text
            WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) = 0 THEN 'due'::text
            WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) <= 3 THEN 'soon'::text
            ELSE 'ok'::text
        END
    END AS stock_status,
    pk.pack_size_g
FROM (
 WITH menu_buys AS (
         SELECT purchases.ingredient_id,
            purchases.qty,
            purchases.unit,
            purchases.total_price,
            purchases.purchase_date
           FROM purchases
          WHERE purchases.usage_type IN ('menu'::usage_type, 'others_personal'::usage_type)
            AND purchases.deleted_at IS NULL
        ), per_unit AS (
         SELECT menu_buys.ingredient_id,
            menu_buys.unit,
            count(*) AS n,
            sum(menu_buys.qty) AS tq
           FROM menu_buys
          GROUP BY menu_buys.ingredient_id, menu_buys.unit
        ), primary_unit AS (
         SELECT DISTINCT ON (per_unit.ingredient_id) per_unit.ingredient_id,
            per_unit.unit
           FROM per_unit
          ORDER BY per_unit.ingredient_id, per_unit.n DESC, per_unit.tq DESC
        ), buys AS (
         SELECT b.ingredient_id,
            b.qty,
            b.unit,
            b.total_price,
            b.purchase_date
           FROM menu_buys b
             JOIN primary_unit u ON u.ingredient_id = b.ingredient_id AND u.unit = b.unit
        ), recent AS (
         SELECT menu_buys.ingredient_id,
            max(menu_buys.purchase_date) AS last_any
           FROM menu_buys
          GROUP BY menu_buys.ingredient_id
        ), agg AS (
         SELECT buys.ingredient_id,
            count(*) AS buys,
            count(DISTINCT buys.purchase_date) AS order_events,
            min(buys.purchase_date) AS first_purchase,
            max(buys.purchase_date) AS last_purchase,
            sum(buys.qty) AS total_qty,
            avg(buys.qty) AS avg_qty,
            sum(buys.total_price) AS total_spent,
            sum(buys.total_price) / NULLIF(sum(buys.qty), 0::numeric) AS avg_unit_cost
           FROM buys
          GROUP BY buys.ingredient_id
        ), last_buy AS (
         SELECT DISTINCT ON (buys.ingredient_id) buys.ingredient_id,
            buys.qty AS last_qty,
            buys.purchase_date AS last_buy_date
           FROM buys
          ORDER BY buys.ingredient_id, buys.purchase_date DESC, buys.qty DESC
        ), calc AS (
         SELECT a.ingredient_id,
            a.buys,
            a.order_events,
            a.first_purchase,
            a.last_purchase,
            a.total_qty,
            a.avg_qty,
            a.total_spent,
            a.avg_unit_cost,
            u.unit AS primary_unit,
            lb.last_qty,
            a.last_purchase - a.first_purchase AS span_days,
                CASE
                    WHEN a.order_events >= 2 THEN (a.last_purchase - a.first_purchase)::numeric / (a.order_events - 1)::numeric
                    ELSE NULL::numeric
                END AS avg_interval_days,
                CASE
                    WHEN a.order_events >= 2 AND (a.last_purchase - a.first_purchase) > 0 THEN (a.total_qty - lb.last_qty) / (a.last_purchase - a.first_purchase)::numeric
                    ELSE NULL::numeric
                END AS daily_consumption
           FROM agg a
             JOIN last_buy lb ON lb.ingredient_id = a.ingredient_id
             JOIN primary_unit u ON u.ingredient_id = a.ingredient_id
        ), today AS (
         SELECT (now() AT TIME ZONE 'Asia/Kolkata'::text)::date AS d
        )
 SELECT i.id AS ingredient_id,
    i.name,
    i.category,
    i.cost_role,
    i.is_active,
    c.primary_unit AS unit,
    c.buys,
    c.order_events,
    c.first_purchase,
    c.last_purchase,
    round(c.last_qty, 2) AS last_qty,
    round(c.avg_qty, 2) AS suggested_order_qty,
    round(c.total_qty, 2) AS total_qty,
    round(c.avg_interval_days, 1) AS avg_interval_days,
    round(COALESCE(i.fixed_daily_usage_qty, c.daily_consumption), 3) AS daily_consumption,
        CASE
            WHEN COALESCE(i.fixed_daily_usage_qty, c.daily_consumption) > 0::numeric THEN round(c.last_qty / COALESCE(i.fixed_daily_usage_qty, c.daily_consumption), 1)
            ELSE NULL::numeric
        END AS days_cover_left,
        CASE
            WHEN c.avg_interval_days IS NOT NULL THEN r.last_any + round(c.avg_interval_days)::integer
            ELSE NULL::date
        END AS next_order_date,
        CASE
            WHEN COALESCE(i.fixed_daily_usage_qty, c.daily_consumption) > 0::numeric THEN r.last_any + floor(c.last_qty / COALESCE(i.fixed_daily_usage_qty, c.daily_consumption))::integer
            ELSE NULL::date
        END AS runout_date,
    t.d AS today,
        CASE
            WHEN c.avg_interval_days IS NOT NULL THEN r.last_any + round(c.avg_interval_days)::integer - t.d
            ELSE NULL::integer
        END AS days_until_due,
    round(c.avg_unit_cost, 2) AS avg_unit_cost,
    round(c.avg_qty * c.avg_unit_cost, 2) AS est_order_cost,
        CASE
            WHEN c.order_events < 2 AND i.fixed_daily_usage_qty IS NULL THEN 'insufficient_history'::text
            WHEN (r.last_any + round(COALESCE(c.avg_interval_days, 1)) ::integer - t.d) < '-1'::integer THEN 'overdue'::text
            WHEN (r.last_any + round(COALESCE(c.avg_interval_days, 1))::integer - t.d) <= 0 THEN 'due'::text
            WHEN (r.last_any + round(COALESCE(c.avg_interval_days, 1))::integer - t.d) <= 3 THEN 'soon'::text
            ELSE 'ok'::text
        END AS status,
    r.last_any AS last_purchase_any,
    t.d - r.last_any AS days_since_purchase,
    (t.d - r.last_any) < LEAST(3, GREATEST(1, floor(COALESCE(c.avg_interval_days, 6::numeric) / 2::numeric)::integer)) AS recently_purchased
   FROM calc c
     JOIN ingredients i ON i.id = c.ingredient_id
     JOIN recent r ON r.ingredient_id = c.ingredient_id
     CROSS JOIN today t
) base
LEFT JOIN (
    SELECT DISTINCT ON (ingredient_id) ingredient_id,
           on_hand_qty,
           counted_at::date AS stock_counted_on
      FROM public.ingredient_stock
     ORDER BY ingredient_id, counted_at DESC
) ls ON ls.ingredient_id = base.ingredient_id
LEFT JOIN public.ingredients pk ON pk.id = base.ingredient_id;
"""


def upgrade() -> None:
    op.execute(sa.text(_ADD_COLUMN))
    op.execute(sa.text(_BACKFILL))
    op.execute(sa.text(_VIEW))


def downgrade() -> None:
    # Restore the 0019 view body (menu-only, no fixed-usage override).
    from alembic.op import execute as _exec  # local import keeps upgrade() import-clean

    _exec(sa.text(
        """
        CREATE OR REPLACE VIEW public.v_ingredient_reorder_forecast AS
        SELECT base.*,
            ls.on_hand_qty,
            ls.stock_counted_on,
            CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
                 THEN round(ls.on_hand_qty / base.daily_consumption, 1) END AS stock_days_cover_left,
            CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
                 THEN ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer END AS stock_runout_date,
            CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0
                 THEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) END AS stock_days_until_due,
            CASE WHEN ls.on_hand_qty IS NOT NULL AND base.daily_consumption > 0 THEN
                CASE
                    WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) < 0 THEN 'overdue'::text
                    WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) = 0 THEN 'due'::text
                    WHEN (ls.stock_counted_on + floor(ls.on_hand_qty / base.daily_consumption)::integer - base.today) <= 3 THEN 'soon'::text
                    ELSE 'ok'::text
                END
            END AS stock_status,
            pk.pack_size_g
        FROM (
         WITH menu_buys AS (
                 SELECT purchases.ingredient_id,
                    purchases.qty,
                    purchases.unit,
                    purchases.total_price,
                    purchases.purchase_date
                   FROM purchases
                  WHERE purchases.usage_type = 'menu'::usage_type AND purchases.deleted_at IS NULL
                ), per_unit AS (
                 SELECT menu_buys.ingredient_id,
                    menu_buys.unit,
                    count(*) AS n,
                    sum(menu_buys.qty) AS tq
                   FROM menu_buys
                  GROUP BY menu_buys.ingredient_id, menu_buys.unit
                ), primary_unit AS (
                 SELECT DISTINCT ON (per_unit.ingredient_id) per_unit.ingredient_id,
                    per_unit.unit
                   FROM per_unit
                  ORDER BY per_unit.ingredient_id, per_unit.n DESC, per_unit.tq DESC
                ), buys AS (
                 SELECT b.ingredient_id,
                    b.qty,
                    b.unit,
                    b.total_price,
                    b.purchase_date
                   FROM menu_buys b
                     JOIN primary_unit u ON u.ingredient_id = b.ingredient_id AND u.unit = b.unit
                ), recent AS (
                 SELECT menu_buys.ingredient_id,
                    max(menu_buys.purchase_date) AS last_any
                   FROM menu_buys
                  GROUP BY menu_buys.ingredient_id
                ), agg AS (
                 SELECT buys.ingredient_id,
                    count(*) AS buys,
                    count(DISTINCT buys.purchase_date) AS order_events,
                    min(buys.purchase_date) AS first_purchase,
                    max(buys.purchase_date) AS last_purchase,
                    sum(buys.qty) AS total_qty,
                    avg(buys.qty) AS avg_qty,
                    sum(buys.total_price) AS total_spent,
                    sum(buys.total_price) / NULLIF(sum(buys.qty), 0::numeric) AS avg_unit_cost
                   FROM buys
                  GROUP BY buys.ingredient_id
                ), last_buy AS (
                 SELECT DISTINCT ON (buys.ingredient_id) buys.ingredient_id,
                    buys.qty AS last_qty,
                    buys.purchase_date AS last_buy_date
                   FROM buys
                  ORDER BY buys.ingredient_id, buys.purchase_date DESC, buys.qty DESC
                ), calc AS (
                 SELECT a.ingredient_id,
                    a.buys,
                    a.order_events,
                    a.first_purchase,
                    a.last_purchase,
                    a.total_qty,
                    a.avg_qty,
                    a.total_spent,
                    a.avg_unit_cost,
                    u.unit AS primary_unit,
                    lb.last_qty,
                    a.last_purchase - a.first_purchase AS span_days,
                        CASE
                            WHEN a.order_events >= 2 THEN (a.last_purchase - a.first_purchase)::numeric / (a.order_events - 1)::numeric
                            ELSE NULL::numeric
                        END AS avg_interval_days,
                        CASE
                            WHEN a.order_events >= 2 AND (a.last_purchase - a.first_purchase) > 0 THEN (a.total_qty - lb.last_qty) / (a.last_purchase - a.first_purchase)::numeric
                            ELSE NULL::numeric
                        END AS daily_consumption
                   FROM agg a
                     JOIN last_buy lb ON lb.ingredient_id = a.ingredient_id
                     JOIN primary_unit u ON u.ingredient_id = a.ingredient_id
                ), today AS (
                 SELECT (now() AT TIME ZONE 'Asia/Kolkata'::text)::date AS d
                )
         SELECT i.id AS ingredient_id,
            i.name,
            i.category,
            i.cost_role,
            i.is_active,
            c.primary_unit AS unit,
            c.buys,
            c.order_events,
            c.first_purchase,
            c.last_purchase,
            round(c.last_qty, 2) AS last_qty,
            round(c.avg_qty, 2) AS suggested_order_qty,
            round(c.total_qty, 2) AS total_qty,
            round(c.avg_interval_days, 1) AS avg_interval_days,
            round(c.daily_consumption, 3) AS daily_consumption,
                CASE
                    WHEN c.daily_consumption > 0::numeric THEN round(c.last_qty / c.daily_consumption, 1)
                    ELSE NULL::numeric
                END AS days_cover_left,
                CASE
                    WHEN c.avg_interval_days IS NOT NULL THEN r.last_any + round(c.avg_interval_days)::integer
                    ELSE NULL::date
                END AS next_order_date,
                CASE
                    WHEN c.daily_consumption > 0::numeric THEN r.last_any + floor(c.last_qty / c.daily_consumption)::integer
                    ELSE NULL::date
                END AS runout_date,
            t.d AS today,
                CASE
                    WHEN c.avg_interval_days IS NOT NULL THEN r.last_any + round(c.avg_interval_days)::integer - t.d
                    ELSE NULL::integer
                END AS days_until_due,
            round(c.avg_unit_cost, 2) AS avg_unit_cost,
            round(c.avg_qty * c.avg_unit_cost, 2) AS est_order_cost,
                CASE
                    WHEN c.order_events < 2 THEN 'insufficient_history'::text
                    WHEN (r.last_any + round(c.avg_interval_days)::integer - t.d) < '-1'::integer THEN 'overdue'::text
                    WHEN (r.last_any + round(c.avg_interval_days)::integer - t.d) <= 0 THEN 'due'::text
                    WHEN (r.last_any + round(c.avg_interval_days)::integer - t.d) <= 3 THEN 'soon'::text
                    ELSE 'ok'::text
                END AS status,
            r.last_any AS last_purchase_any,
            t.d - r.last_any AS days_since_purchase,
            (t.d - r.last_any) < LEAST(3, GREATEST(1, floor(COALESCE(c.avg_interval_days, 6::numeric) / 2::numeric)::integer)) AS recently_purchased
           FROM calc c
             JOIN ingredients i ON i.id = c.ingredient_id
             JOIN recent r ON r.ingredient_id = c.ingredient_id
             CROSS JOIN today t
        ) base
        LEFT JOIN (
            SELECT DISTINCT ON (ingredient_id) ingredient_id,
                   on_hand_qty,
                   counted_at::date AS stock_counted_on
              FROM public.ingredient_stock
             ORDER BY ingredient_id, counted_at DESC
        ) ls ON ls.ingredient_id = base.ingredient_id
        LEFT JOIN public.ingredients pk ON pk.id = base.ingredient_id;
        """
    ))
    _exec(sa.text(
        "ALTER TABLE public.ingredients DROP COLUMN IF EXISTS fixed_daily_usage_qty;"
    ))
