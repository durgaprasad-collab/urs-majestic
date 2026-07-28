"""On-hand stock counts + consumption-based reorder forecast.

The reorder forecast was pure purchase-cadence: it fired when
today >= last_purchase + avg_interval_days, and inferred remaining stock from
the last purchased quantity. That produced false alarms (Cabbage flagged
overdue with 2 kg actually on the shelf) and missed real ones, because it had
no idea what is physically in the kitchen.

This adds:
1. ingredient_stock -- an append-only log of owner-entered on-hand counts
   (latest count per ingredient = current stock). Kept, never overwritten.
2. Baby Corn pack_size_g = 200 so a count entered in packets converts to kg.
3. v_ingredient_reorder_forecast wrapped to append stock-aware columns:
   when a count exists AND daily_consumption > 0, stock_* gives
   runout = counted_at + on_hand/daily_consumption and a status off that. The
   existing 26 cadence columns are preserved verbatim (append-only), so rows
   with no count keep the old cadence behaviour and nothing downstream breaks.
   The route prefers stock_* when present, else falls back to cadence.

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = """
CREATE TABLE IF NOT EXISTS public.ingredient_stock (
    id            bigserial PRIMARY KEY,
    ingredient_id integer     NOT NULL REFERENCES public.ingredients(id),
    on_hand_qty   numeric     NOT NULL CHECK (on_hand_qty >= 0),
    unit          text        NOT NULL,     -- stored in the ingredient's forecast (primary) unit
    counted_at    timestamptz NOT NULL DEFAULT now(),
    counted_by    integer     NULL REFERENCES public.users(id),
    note          text        NULL
);
CREATE INDEX IF NOT EXISTS ix_ingredient_stock_latest
    ON public.ingredient_stock (ingredient_id, counted_at DESC);

COMMENT ON TABLE public.ingredient_stock IS
  'Append-only owner-entered on-hand stock counts. Latest counted_at per ingredient_id is the current stock the reorder forecast uses. on_hand_qty is in the ingredient''s forecast primary unit (same unit as v_ingredient_reorder_forecast.daily_consumption).';

-- Baby Corn is bought in kg but counted in packets (200 g each). Setting
-- pack_size_g lets the entry form offer a "packet" option and convert to kg.
UPDATE public.ingredients SET pack_size_g = 200
 WHERE lower(name) = 'baby corn' AND pack_size_g IS DISTINCT FROM 200;
"""

# The view is the existing v_ingredient_reorder_forecast body verbatim, wrapped
# as subquery `base` so base.* preserves all 26 columns in order; stock columns
# are appended. Only additive -> CREATE OR REPLACE is happy.
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


def upgrade() -> None:
    op.execute(sa.text(_TABLE))
    op.execute(sa.text(_VIEW))


def downgrade() -> None:
    # Restore the pre-stock view (no stock columns) and drop the table.
    op.execute(sa.text("DROP VIEW IF EXISTS public.v_ingredient_reorder_forecast;"))
    op.execute(sa.text("DROP TABLE IF EXISTS public.ingredient_stock;"))
    raise NotImplementedError(
        "0019 partial downgrade dropped the stock table/columns but the view "
        "must be recreated from 0015's definition by hand. Forward-only in "
        "practice (dev == prod)."
    )
