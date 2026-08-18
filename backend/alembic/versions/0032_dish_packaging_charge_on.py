"""Add dish_packaging_map.charge_on — lets a packaging row be priced on
DINE-IN orders instead of parcel orders.

_dish_packaging_cost() previously weighted every dish_packaging_map row by
the same _parcel_rate uniformly, which is correct for containers (only used
when an order IS a parcel) but backwards for items like Disposable Plates,
which are used precisely when an order is NOT a parcel (owner-confirmed
2026-08-17: "everything is dine-in until the order carries a parcel in it" --
the same detection logic _parcel_rate already uses, just inverted).

charge_on defaults to 'parcel' (preserves every existing row's behavior
exactly -- this migration changes zero existing costs). 'dine_in' rows get
weighted by (1 - parcel_rate) in cost_engine.py instead.

Revision ID: 0032
Revises: 0031
"""

from alembic import op
import sqlalchemy as sa


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        """
        ALTER TABLE public.dish_packaging_map
            ADD COLUMN IF NOT EXISTS charge_on text NOT NULL DEFAULT 'parcel';

        ALTER TABLE public.dish_packaging_map
            DROP CONSTRAINT IF EXISTS dish_packaging_map_charge_on_check;
        ALTER TABLE public.dish_packaging_map
            ADD CONSTRAINT dish_packaging_map_charge_on_check
            CHECK (charge_on = ANY (ARRAY['parcel', 'dine_in']));

        COMMENT ON COLUMN public.dish_packaging_map.charge_on IS
          'Which order type this container/item is used on: ''parcel'' (default, weighted by _parcel_rate -- containers only used when parceled) or ''dine_in'' (weighted by 1 - _parcel_rate -- e.g. Disposable Plates, used precisely when an order is NOT a parcel).';
        """
    ))


def downgrade() -> None:
    op.execute(sa.text(
        """
        ALTER TABLE public.dish_packaging_map DROP CONSTRAINT IF EXISTS dish_packaging_map_charge_on_check;
        ALTER TABLE public.dish_packaging_map DROP COLUMN IF EXISTS charge_on;
        """
    ))
