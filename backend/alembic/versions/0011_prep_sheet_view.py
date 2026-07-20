"""Add v_prep_sheet — rolling kitchen prep guidance per item per day-of-week.

Descriptive statistics over a trailing 28-day window. Deliberately NOT a
forecast: at current volume (~36 units/day, CV ~65%, top item under 4/day,
56 of 75 items below 0.5/day) a fitted model's intervals would be wider than
the quantities themselves.

The one thing this view gets right that a naive query would not: zero-sale
trading days are counted as zero rather than dropped. Averaging only the days
an item sold inflates every figure — the classic way a prep sheet lies. The
grid CROSS JOIN of items x open days exists for that reason.

`open_days` is derived from item_sales, so days the restaurant did not trade
never enter the denominator.

CREATE OR REPLACE VIEW — additive/idempotent; safe on the live DB.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VIEW = """
CREATE OR REPLACE VIEW v_prep_sheet AS
with ref as (
  select max(sale_date) as ref_date from item_sales
),
open_days as (
  select distinct sale_date from item_sales, ref
  where sale_date > ref.ref_date - 28
),
items as (
  select distinct menu_item_id, item
  from v_item_sales_canonical
  where menu_item_id is not null
),
grid as (
  select i.menu_item_id, i.item, d.sale_date
  from items i cross join open_days d
),
filled as (
  select g.menu_item_id, g.item, g.sale_date,
         coalesce(sum(s.qty), 0) as qty
  from grid g
  left join v_item_sales_canonical s
    on s.menu_item_id = g.menu_item_id and s.sale_date = g.sale_date
  group by 1,2,3
),
overall as (
  select f.menu_item_id, f.item,
    sum(f.qty) filter (where f.sale_date > r.ref_date - 7)                           as qty_7d,
    avg(f.qty) filter (where f.sale_date > r.ref_date - 7)                           as avg_7d,
    max(f.qty) filter (where f.sale_date > r.ref_date - 7)                           as max_7d,
    count(*)   filter (where f.sale_date > r.ref_date - 7 and f.qty > 0)             as days_sold_7d,
    avg(f.qty) filter (where f.sale_date between r.ref_date - 13 and r.ref_date - 7) as avg_prior_7d,
    avg(f.qty)                                                                       as avg_28d
  from filled f cross join ref r
  group by 1,2
),
by_dow as (
  select f.menu_item_id,
         extract(dow from f.sale_date)::int as dow,
         avg(f.qty)  as avg_dow,
         max(f.qty)  as max_dow,
         count(*)    as dow_samples
  from filled f
  group by 1,2
)
select
  o.menu_item_id,
  o.item,
  d.dow,
  to_char(date '2024-01-07' + d.dow, 'Dy')                       as dow_name,
  round(o.qty_7d, 1)                                             as qty_7d,
  round(o.avg_7d, 2)                                             as avg_7d,
  round(o.max_7d, 1)                                             as max_7d,
  o.days_sold_7d,
  round(o.avg_28d, 2)                                            as avg_28d,
  round(d.avg_dow, 2)                                            as avg_same_dow,
  round(d.max_dow, 1)                                            as max_same_dow,
  d.dow_samples,
  ceil(greatest(coalesce(d.avg_dow, 0), coalesce(o.avg_7d, 0)))  as prep_qty_suggested,
  greatest(coalesce(d.max_dow, 0), coalesce(o.max_7d, 0))        as prep_qty_upper,
  case
    when o.avg_prior_7d is null or o.avg_prior_7d = 0 then null
    else round(((o.avg_7d - o.avg_prior_7d) / o.avg_prior_7d) * 100, 0)
  end                                                            as trend_pct_vs_prior_7d,
  case
    when o.avg_28d >= 1 then 'core'
    when o.avg_28d >= 0.25 then 'occasional'
    else 'tail'
  end                                                            as demand_band
from overall o
join by_dow d on d.menu_item_id = o.menu_item_id
"""

_COMMENT = """
COMMENT ON VIEW v_prep_sheet IS
'Rolling prep guidance per menu item per day-of-week. Descriptive statistics only
over a trailing 28-day window (zero-sale open days counted as 0) - NOT a forecast.
prep_qty_suggested = ceil(max(same-dow 28d mean, overall 7d mean)); prep_qty_upper
= observed max. Sample sizes are small (dow_samples ~4): treat tail-band items as
judgement calls, not instructions.'
"""


def upgrade() -> None:
    op.execute(sa.text(_VIEW))
    op.execute(sa.text(_COMMENT))


def downgrade() -> None:
    op.execute(sa.text("DROP VIEW IF EXISTS v_prep_sheet"))
