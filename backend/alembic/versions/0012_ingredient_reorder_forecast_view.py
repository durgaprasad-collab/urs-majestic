"""Add v_ingredient_reorder_forecast — per-ingredient reorder date + quantity.

An empirical *purchase-cadence* forecast, not a recipe model. For each menu
ingredient it answers two questions the owner can act on (and automation can
later read): when is the next order due, and how much to order.

Why cadence and not recipe-based consumption: at ~1 month of history, with only
half the ingredients carrying portion sizes and purchase units that are
inconsistent per ingredient (kg vs g vs pcs), converting recipe grams into
purchase units would produce more nulls and wrong numbers than signal. Cadence
stays entirely inside each ingredient's own purchase unit, needs nothing but the
purchases table, and degrades gracefully — an ingredient with fewer than two
distinct order dates is flagged `insufficient_history` rather than guessed.

The forecast, per ingredient (menu-usage purchases only):
  - primary_unit      = the unit it is most often bought in; all qty math below
                        is confined to purchases in that unit, so mixed-unit
                        history never corrupts an average.
  - avg_interval_days = mean gap between DISTINCT order dates. Two purchase lines
                        on the same day are one ordering event, not two.
  - next_order_date   = last_purchase + avg_interval_days  (the chosen trigger:
                        due on/after the ingredient's own typical interval).
  - suggested_order_qty = average quantity per purchase, in primary_unit.
  - daily_consumption = (total bought - last buy) / span between first and last
                        order — an implied burn rate. Feeds runout_date, shown as
                        a secondary cross-check on the cadence date.
  - status            = insufficient_history | overdue | due | soon | ok,
                        relative to IST "today" (Asia/Kolkata), matching the rest
                        of the app's business-date convention.

CREATE OR REPLACE VIEW — additive/idempotent; safe on the live DB.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VIEW = """
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
  end                                                           as status
from calc c
join ingredients i on i.id = c.ingredient_id
cross join today t
"""

_COMMENT = """
COMMENT ON VIEW v_ingredient_reorder_forecast IS
'Per-ingredient reorder forecast from purchase cadence (menu-usage purchases only).
next_order_date = last_purchase + mean gap between distinct order dates;
suggested_order_qty = mean qty per purchase, in the ingredient''s most-used unit.
daily_consumption / runout_date are an implied-burn cross-check, not the trigger.
status is relative to IST today. Descriptive, not a fitted forecast: an ingredient
with under two distinct order dates is insufficient_history, never guessed.'
"""


def upgrade() -> None:
    op.execute(sa.text(_VIEW))
    op.execute(sa.text(_COMMENT))


def downgrade() -> None:
    op.execute(sa.text("DROP VIEW IF EXISTS v_ingredient_reorder_forecast"))
