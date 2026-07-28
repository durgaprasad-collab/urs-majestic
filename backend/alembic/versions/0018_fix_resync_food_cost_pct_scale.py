"""Fix resync_derived_costs() 100x scale bug + repair poisoned rows.

menu_items.derived_food_cost_pct is a FRACTION (0-1) everywhere it is written
by the Python cost engine (cost_engine.py: cost/price -> 0.3297) and everywhere
it is read (analysis.py's `1 - fcp`, daily_brief_v3, kpi_governance, the 0.35
placeholder, the Numeric(7,4) column). But the SQL resync_derived_costs()
function from 0015 wrote `v.food_cost_pct`, and v_menu_item_cost defines that as
`round(100 * cost/price, 2)` -- a PERCENTAGE (32.97). purchase_routes.py calls
this function after every purchase insert/edit/soft-delete, so it had stamped
the whole active menu 100x too high. The Analysis tab then rendered
`1 - 32.97 = -3197%` margins; Daily Brief food-cost and KPI gross-profit were
likewise inflated.

Fix: divide by 100 so the function stores a fraction, matching the Python path
and every consumer. Then re-run it once to repair the already-poisoned rows.
derived_cost_per_unit = v.food_cost was and stays correct (absolute rupees).

Revision ID: 0018
Revises: 0017
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Only change from 0015: `v.food_cost_pct` -> `round(v.food_cost_pct / 100.0, 4)`
# in both the SET and the change-detection guard. round(...,4) matches the
# Numeric(7,4) column and the Python quantize('0.0001').
_FIX_FN = """
CREATE OR REPLACE FUNCTION public.resync_derived_costs()
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE n integer;
BEGIN
  UPDATE public.menu_items mi
     SET derived_cost_per_unit = v.food_cost,
         derived_food_cost_pct = round(v.food_cost_pct / 100.0, 4)
    FROM public.v_menu_item_cost v
   WHERE v.id = mi.id
     AND (mi.derived_cost_per_unit IS DISTINCT FROM v.food_cost
       OR mi.derived_food_cost_pct IS DISTINCT FROM round(v.food_cost_pct / 100.0, 4));
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

COMMENT ON FUNCTION public.resync_derived_costs() IS
  'Repoints menu_items.derived_cost_per_unit (absolute rupees) / derived_food_cost_pct (FRACTION 0-1) at v_menu_item_cost. food_cost_pct in the view is a 0-100 percentage, so it is divided by 100 here to match the fraction convention used by cost_engine.py and every consumer. Call after ANY purchase insert, edit or soft-delete.';
"""


def upgrade() -> None:
    op.execute(sa.text(_FIX_FN))
    # Repair the rows the buggy function already poisoned (100x too high).
    op.execute(sa.text("SELECT resync_derived_costs()"))


def downgrade() -> None:
    raise NotImplementedError(
        "0018 is forward-only. It corrects a 100x scale error in "
        "derived_food_cost_pct; reverting would re-poison every dish's cost "
        "figure and the analysis/brief/KPI numbers built on it."
    )
