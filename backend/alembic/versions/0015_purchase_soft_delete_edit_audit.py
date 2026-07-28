"""Purchase soft delete, edit audit trail and cost resync.

Purchases are financial records. Until now the admin panel could edit any of
them in place with no record of what changed, and had no way to remove one at
all. This revision makes deletion possible without destroying history, and
makes both edit and deletion impossible to perform silently.

Four things happen here.

1. purchases gains deleted_at / deleted_by / delete_reason, guarded by a CHECK
   constraint so a partial soft delete cannot exist: a row cannot be marked
   deleted without both an actor and a reason of at least 10 characters.

2. Every view that reads purchases learns about the flag. This is the part that
   is easy to get wrong. Adding the column without the predicates would give a
   delete button that hides a row from the screen while it stays fully priced
   into every cost figure — worse than no button, because it manufactures
   confidence that a bad row is gone. Two subtleties:
     - v_ingredient_cost anchors its 45-day recency window on
       (SELECT max(purchase_date) FROM purchases). Deleting the newest purchase
       would slide that window backwards and reprice ingredients unrelated to
       the deleted row, so the anchor itself is filtered.
     - v_packaging_per_order references purchases three times (the sum, and the
       min/max date bounds). All three are filtered.

3. cost_base_repair_log gains actor_user_id, so a logged change names a person.

4. purchases gains row_version for optimistic locking. Three people enter
   purchases concurrently; without it, two people editing the same row means
   last-write-wins and one correction vanishes with no error.

Plus resync_derived_costs(): menu_items.derived_cost_per_unit and
derived_food_cost_pct are frozen snapshots of v_menu_item_cost with no refresh
path. Any purchase change leaves them stale until this is called.

This revision is idempotent and was applied to the live database ahead of it;
it exists so a clean deploy from migrations builds the same schema.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MIN_DELETE_REASON = 10

_COLUMNS = f"""
ALTER TABLE public.purchases
  ADD COLUMN IF NOT EXISTS deleted_at    timestamptz NULL,
  ADD COLUMN IF NOT EXISTS deleted_by    integer     NULL REFERENCES public.users(id),
  ADD COLUMN IF NOT EXISTS delete_reason text        NULL,
  ADD COLUMN IF NOT EXISTS row_version   integer     NOT NULL DEFAULT 1;

COMMENT ON COLUMN public.purchases.deleted_at IS
  'Soft delete. NULL = live. Every view that reads purchases filters on deleted_at IS NULL. Never hard-delete a purchase row.';

COMMENT ON COLUMN public.purchases.row_version IS
  'Optimistic lock counter. SQLAlchemy version_id_col: every UPDATE carries the expected value in its WHERE clause and increments it. A concurrent edit raises StaleDataError instead of silently overwriting.';

ALTER TABLE public.purchases DROP CONSTRAINT IF EXISTS purchases_soft_delete_complete;
ALTER TABLE public.purchases
  ADD CONSTRAINT purchases_soft_delete_complete CHECK (
    (deleted_at IS NULL AND deleted_by IS NULL AND delete_reason IS NULL)
    OR
    (deleted_at IS NOT NULL AND deleted_by IS NOT NULL
     AND delete_reason IS NOT NULL AND length(btrim(delete_reason)) >= {MIN_DELETE_REASON})
  );

ALTER TABLE public.cost_base_repair_log
  ADD COLUMN IF NOT EXISTS actor_user_id integer NULL REFERENCES public.users(id);

COMMENT ON COLUMN public.cost_base_repair_log.actor_user_id IS
  'users.id of the person who made the change. NULL for pre-2026-07-28 assistant-run repairs.';
"""

# Column lists, names, order and types are unchanged from the prior definitions,
# so CREATE OR REPLACE succeeds with dependent views in place. The ONLY change
# in each is the deleted_at predicate.
_VIEWS = """
CREATE OR REPLACE VIEW v_purchase_normalised AS
SELECT p.id,
    p.ingredient_id,
    p.purchase_date,
    p.total_price,
    p.usage_type::text AS usage_type,
    CASE
        WHEN p.unit::text = ANY (ARRAY['kg'::text, 'g'::text, 'l'::text, 'ml'::text]) THEN 'gml'::text
        WHEN i.pack_size_g IS NOT NULL THEN 'gml'::text
        ELSE 'pc'::text
    END AS base_unit,
    CASE p.unit::text
        WHEN 'kg'::text THEN p.qty * 1000::numeric
        WHEN 'l'::text THEN p.qty * 1000::numeric
        WHEN 'g'::text THEN p.qty
        WHEN 'ml'::text THEN p.qty
        ELSE
        CASE
            WHEN i.pack_size_g IS NOT NULL THEN p.qty * i.pack_size_g
            ELSE p.qty
        END
    END AS base_qty
   FROM purchases p
     JOIN ingredients i ON i.id = p.ingredient_id
  WHERE p.deleted_at IS NULL;

CREATE OR REPLACE VIEW v_ingredient_cost AS
 WITH agg AS (
         SELECT v_purchase_normalised.ingredient_id,
            v_purchase_normalised.base_unit,
            sum(v_purchase_normalised.total_price) AS spend,
            sum(v_purchase_normalised.base_qty) AS qty,
            count(*) AS n,
            max(v_purchase_normalised.purchase_date) AS last_buy
           FROM v_purchase_normalised
          WHERE v_purchase_normalised.usage_type = 'menu'::text
          GROUP BY v_purchase_normalised.ingredient_id, v_purchase_normalised.base_unit
        ), recent AS (
         SELECT v_purchase_normalised.ingredient_id,
            v_purchase_normalised.base_unit,
            sum(v_purchase_normalised.total_price) AS spend,
            sum(v_purchase_normalised.base_qty) AS qty,
            count(*) AS n
           FROM v_purchase_normalised
          WHERE v_purchase_normalised.usage_type = 'menu'::text
            AND v_purchase_normalised.purchase_date >= (((
                 SELECT max(purchases.purchase_date) AS max
                   FROM purchases
                  WHERE purchases.deleted_at IS NULL)) - '45 days'::interval)
          GROUP BY v_purchase_normalised.ingredient_id, v_purchase_normalised.base_unit
        ), pick AS (
         SELECT a.ingredient_id,
            a.base_unit,
            a.spend,
            a.qty,
            a.n,
            a.last_buy,
            row_number() OVER (PARTITION BY a.ingredient_id ORDER BY a.n DESC, a.last_buy DESC) AS rn,
            sum(a.n) OVER (PARTITION BY a.ingredient_id) AS n_all
           FROM agg a
        )
 SELECT i.id AS ingredient_id,
    i.name,
    i.cost_role::text AS cost_role,
    p.base_unit,
    round(COALESCE(r.spend / NULLIF(r.qty, 0::numeric), p.spend / NULLIF(p.qty, 0::numeric)), 6) AS cost_per_base_unit,
    COALESCE(r.n, p.n, 0::bigint) AS purchases_used,
    COALESCE(p.n_all, 0::numeric) - COALESCE(p.n, 0::bigint)::numeric AS purchases_ignored_other_unit,
        CASE
            WHEN COALESCE(r.n, p.n, 0::bigint) = 0 THEN 'none'::text
            WHEN COALESCE(r.n, p.n, 0::bigint) = 1 THEN 'building'::text
            ELSE 'reliable'::text
        END::cost_confidence_type AS confidence
   FROM ingredients i
     LEFT JOIN pick p ON p.ingredient_id = i.id AND p.rn = 1
     LEFT JOIN recent r ON r.ingredient_id = i.id AND r.base_unit = p.base_unit
  WHERE i.is_active;

CREATE OR REPLACE VIEW v_packaging_per_order AS
 SELECT round(sum(total_price) / NULLIF((
         SELECT sum(d.orders) AS sum
           FROM daily_channel_sales d
          WHERE d.business_date >= ((
                 SELECT min(purchases.purchase_date) AS min
                   FROM purchases
                  WHERE purchases.deleted_at IS NULL
                    AND (purchases.ingredient_id IN (
                         SELECT ingredients.id
                           FROM ingredients
                          WHERE ingredients.cost_role::text = 'per_order'::text))))
            AND d.business_date <= ((
                 SELECT max(purchases.purchase_date) AS max
                   FROM purchases
                  WHERE purchases.deleted_at IS NULL))), 0)::numeric, 2) AS packaging_per_order,
    sum(total_price) AS packaging_spend
   FROM purchases p
  WHERE deleted_at IS NULL
    AND (ingredient_id IN (
         SELECT ingredients.id
           FROM ingredients
          WHERE ingredients.cost_role::text = 'per_order'::text));
"""

_RESYNC = """
CREATE OR REPLACE FUNCTION public.resync_derived_costs()
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE n integer;
BEGIN
  UPDATE public.menu_items mi
     SET derived_cost_per_unit = v.food_cost,
         derived_food_cost_pct = v.food_cost_pct
    FROM public.v_menu_item_cost v
   WHERE v.id = mi.id
     AND (mi.derived_cost_per_unit IS DISTINCT FROM v.food_cost
       OR mi.derived_food_cost_pct IS DISTINCT FROM v.food_cost_pct);
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

COMMENT ON FUNCTION public.resync_derived_costs() IS
  'Repoints menu_items.derived_cost_per_unit / derived_food_cost_pct at v_menu_item_cost. Call after ANY purchase insert, edit or soft-delete, or the menu carries stale costs. Returns rows changed.';
"""

# v_ingredient_reorder_forecast reads `purchases` directly in its menu_buys CTE.
# CREATE OR REPLACE requires the whole body, so the full definition is inlined
# below, copied verbatim from pg_get_viewdef on the live database. The only
# difference from 0014 is the `deleted_at IS NULL` predicate in menu_buys.
_REORDER = """
CREATE OR REPLACE VIEW v_ingredient_reorder_forecast AS
 WITH menu_buys AS (
         SELECT purchases.ingredient_id,
            purchases.qty,
            purchases.unit,
            purchases.total_price,
            purchases.purchase_date
           FROM purchases
          WHERE purchases.usage_type = 'menu'::usage_type
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
     CROSS JOIN today t;
"""


def upgrade() -> None:
    op.execute(sa.text(_COLUMNS))
    op.execute(sa.text(_VIEWS))
    op.execute(sa.text(_REORDER))
    op.execute(sa.text(_RESYNC))


def downgrade() -> None:
    # Forward-only in practice (dev == prod). The columns are deliberately NOT
    # dropped on downgrade: doing so would destroy the record of which rows had
    # been deleted and why. Only the predicates are reverted, by re-running the
    # prior revisions' view definitions.
    raise NotImplementedError(
        "0015 is forward-only. Dropping the soft-delete columns would destroy "
        "audit history. Revert the view predicates by hand if genuinely needed."
    )
