"""Reorder forecast: anchor the projection on the latest purchase of ANY unit.

Before this, next_order_date / runout_date / days_until_due / status were all
projected from `last_purchase` — the most recent purchase in the ingredient's
PRIMARY unit only. So a purchase logged in a different unit (kg vs g, pcs vs
bag) did not move the forecast: the item kept showing "overdue" even though it
had just been bought. Confirmed live on Gobi, Ginger-Garlic and Spring Onion,
each of which had a newer purchase in a non-primary unit.

Fix: anchor the forward projection on `last_purchase_any` (max purchase_date
across ALL units, already computed in the `recent` CTE). A purchase on any later
date now resets the calculation from that date. The cadence interval and all
quantity aggregates stay primary-unit based (dates are unit-agnostic; quantities
are not) — only the anchor the interval is added to changes.

Output columns, names, order and types are unchanged, so CREATE OR REPLACE VIEW
is valid. Idempotent; safe on the live DB.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# An ingredient purchased within this many days is treated as freshly stocked and
# suppressed from the reorder list. "last 2-3 days" -> a 3-day window.
_RECENT_DAYS = 3

_VIEW = f"""
CREATE OR REPLACE VIEW v_ingredient_reorder_forecast AS
with menu_buys as (
  -- Only menu-usage purchases are reordered for the business; personal buys
  -- are excluded so they never enter a supplier order.
  select ingredient_id, qty, unit, total_price, purchase_date
  from purchases
  where usage_type = 'menu'
),
per_unit as (
  select ingredient_id, unit, count(*) as n, sum(qty) as tq
  from menu_buys group by ingredient_id, unit
),
primary_unit as (
  -- The unit this ingredient is bought in most often. All quantity aggregates
  -- are restricted to this unit so a stray kg-vs-g row can't skew an average.
  select distinct on (ingredient_id) ingredient_id, unit
  from per_unit order by ingredient_id, n desc, tq desc
),
buys as (
  select b.*
  from menu_buys b
  join primary_unit u on u.ingredient_id = b.ingredient_id and u.unit = b.unit
),
recent as (
  -- Latest purchase across ALL units. This anchors the forward projection, so a
  -- purchase in any unit / on any later date resets the forecast from that date.
  select ingredient_id, max(purchase_date) as last_any
  from menu_buys group by ingredient_id
),
agg as (
  select
    ingredient_id,
    count(*)                              as buys,
    count(distinct purchase_date)         as order_events,
    min(purchase_date)                    as first_purchase,
    max(purchase_date)                    as last_purchase,
    sum(qty)                              as total_qty,
    avg(qty)                              as avg_qty,
    sum(total_price)                      as total_spent,
    sum(total_price) / nullif(sum(qty), 0) as avg_unit_cost
  from buys group by ingredient_id
),
last_buy as (
  -- Quantity of the most recent purchase — the stock notionally still on hand.
  select distinct on (ingredient_id)
    ingredient_id, qty as last_qty, purchase_date as last_buy_date
  from buys order by ingredient_id, purchase_date desc, qty desc
),
calc as (
  select
    a.*,
    u.unit as primary_unit,
    lb.last_qty,
    (a.last_purchase - a.first_purchase) as span_days,
    -- Cadence needs at least two DISTINCT order dates. Same-day multi-line
    -- purchases collapse to one event, so they read as insufficient, not as a
    -- zero-day interval.
    case when a.order_events >= 2
         then (a.last_purchase - a.first_purchase)::numeric / (a.order_events - 1)
    end as avg_interval_days,
    case when a.order_events >= 2 and (a.last_purchase - a.first_purchase) > 0
         then (a.total_qty - lb.last_qty) / (a.last_purchase - a.first_purchase)
    end as daily_consumption
  from agg a
  join last_buy lb on lb.ingredient_id = a.ingredient_id
  join primary_unit u on u.ingredient_id = a.ingredient_id
),
today as (select (now() at time zone 'Asia/Kolkata')::date as d)
select
  i.id                                                          as ingredient_id,
  i.name,
  i.category,
  i.cost_role,
  i.is_active,
  c.primary_unit                                                as unit,
  c.buys,
  c.order_events,
  c.first_purchase,
  c.last_purchase,
  round(c.last_qty, 2)                                          as last_qty,
  round(c.avg_qty, 2)                                           as suggested_order_qty,
  round(c.total_qty, 2)                                         as total_qty,
  round(c.avg_interval_days, 1)                                 as avg_interval_days,
  round(c.daily_consumption, 3)                                 as daily_consumption,
  case when c.daily_consumption > 0
       then round(c.last_qty / c.daily_consumption, 1) end      as days_cover_left,
  -- Projections anchor on r.last_any (latest purchase, ANY unit), so a purchase
  -- on a later date resets the forecast from that date. r.last_any is always >=
  -- c.last_purchase (max over a superset), so it is the correct anchor.
  case when c.avg_interval_days is not null
       then r.last_any + round(c.avg_interval_days)::int end    as next_order_date,
  case when c.daily_consumption > 0
       then r.last_any + floor(c.last_qty / c.daily_consumption)::int end as runout_date,
  t.d                                                           as today,
  case when c.avg_interval_days is not null
       then (r.last_any + round(c.avg_interval_days)::int) - t.d end as days_until_due,
  round(c.avg_unit_cost, 2)                                     as avg_unit_cost,
  round(c.avg_qty * c.avg_unit_cost, 2)                         as est_order_cost,
  case
    when c.order_events < 2 then 'insufficient_history'
    when (r.last_any + round(c.avg_interval_days)::int) - t.d < -1 then 'overdue'
    when (r.last_any + round(c.avg_interval_days)::int) - t.d <= 0 then 'due'
    when (r.last_any + round(c.avg_interval_days)::int) - t.d <= 3 then 'soon'
    else 'ok'
  end                                                           as status,
  r.last_any                                                    as last_purchase_any,
  (t.d - r.last_any)                                            as days_since_purchase,
  (t.d - r.last_any) <= {_RECENT_DAYS}                          as recently_purchased
from calc c
join ingredients i on i.id = c.ingredient_id
join recent r on r.ingredient_id = c.ingredient_id
cross join today t
"""

_COMMENT = f"""
COMMENT ON VIEW v_ingredient_reorder_forecast IS
'Per-ingredient reorder forecast from purchase cadence (menu-usage purchases only).
next_order_date = latest purchase of ANY unit + mean gap between distinct primary-unit
order dates; a purchase on any later date resets the projection from that date.
suggested_order_qty = mean qty per purchase, in the ingredient''s most-used unit.
daily_consumption / runout_date are an implied-burn cross-check, not the trigger.
recently_purchased = a purchase (any unit) landed within {_RECENT_DAYS} days -> treat as
freshly stocked and skip; automation should filter `and not recently_purchased`.
status is relative to IST today. Descriptive, not a fitted forecast: an ingredient
with under two distinct order dates is insufficient_history, never guessed.'
"""


def upgrade() -> None:
    op.execute(sa.text(_VIEW))
    op.execute(sa.text(_COMMENT))


def downgrade() -> None:
    # Forward-only in practice (dev == prod). Re-running 0013's upgrade restores
    # the prior view (anchored on primary-unit last_purchase) via CREATE OR REPLACE.
    op.execute(sa.text("DROP VIEW IF EXISTS v_ingredient_reorder_forecast"))
