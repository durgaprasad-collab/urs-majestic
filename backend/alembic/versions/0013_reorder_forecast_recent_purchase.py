"""Add recent-purchase suppression to v_ingredient_reorder_forecast.

An ingredient bought in the last few days is freshly stocked — nagging to
reorder it is noise, even when its cadence math says it's "due" (e.g. a stale
last_purchase in the primary unit while a real purchase landed in another unit).

Adds three columns so both the page and any downstream automation can skip these:
  - last_purchase_any   = most recent purchase date across ALL menu-usage
                          purchases (any unit), not just the primary-unit rows
                          the cadence math uses.
  - days_since_purchase = IST today - last_purchase_any.
  - recently_purchased  = true when that gap is within RECENT_DAYS (3). The
                          reorder page drops these from the to-order list;
                          automation should filter `and not recently_purchased`.

Nothing else about the forecast changes. CREATE OR REPLACE VIEW — additive and
idempotent; safe on the live DB.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
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
  -- Latest purchase across ALL units — the "did we just buy this?" signal, which
  -- must see a purchase logged in a non-primary unit that the cadence math skips.
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
  case when c.avg_interval_days is not null
       then c.last_purchase + round(c.avg_interval_days)::int end as next_order_date,
  case when c.daily_consumption > 0
       then c.last_purchase + floor(c.last_qty / c.daily_consumption)::int end as runout_date,
  t.d                                                           as today,
  case when c.avg_interval_days is not null
       then (c.last_purchase + round(c.avg_interval_days)::int) - t.d end as days_until_due,
  round(c.avg_unit_cost, 2)                                     as avg_unit_cost,
  round(c.avg_qty * c.avg_unit_cost, 2)                         as est_order_cost,
  case
    when c.order_events < 2 then 'insufficient_history'
    when (c.last_purchase + round(c.avg_interval_days)::int) - t.d < -1 then 'overdue'
    when (c.last_purchase + round(c.avg_interval_days)::int) - t.d <= 0 then 'due'
    when (c.last_purchase + round(c.avg_interval_days)::int) - t.d <= 3 then 'soon'
    else 'ok'
  end                                                           as status,
  -- New columns appended after `status`: CREATE OR REPLACE VIEW can only add
  -- columns at the end, never reorder existing ones.
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
next_order_date = last_purchase + mean gap between distinct order dates;
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
    # Forward-only in practice (dev == prod). Re-running 0012's upgrade restores
    # the prior view via CREATE OR REPLACE.
    op.execute(sa.text("DROP VIEW IF EXISTS v_ingredient_reorder_forecast"))
