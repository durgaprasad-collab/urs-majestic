"""Add 'always' to dish_packaging_map.charge_on -- for items used on every
order regardless of parcel/dine-in (e.g. Disposable Spoons), where neither
existing option is correct: 'parcel' would undercount the dine-in occurrences
and 'dine_in' would undercount the parcel occurrences. 'always' is weighted
at full qty (rate=1, no parcel_rate fraction applied) in cost_engine.py.

Revision ID: 0033
Revises: 0032
"""

from alembic import op
import sqlalchemy as sa


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        """
        ALTER TABLE public.dish_packaging_map
            DROP CONSTRAINT IF EXISTS dish_packaging_map_charge_on_check;
        ALTER TABLE public.dish_packaging_map
            ADD CONSTRAINT dish_packaging_map_charge_on_check
            CHECK (charge_on = ANY (ARRAY['parcel', 'dine_in', 'always']));

        COMMENT ON COLUMN public.dish_packaging_map.charge_on IS
          'Which order type this container/item is used on: ''parcel'' (weighted by _parcel_rate), ''dine_in'' (weighted by 1 - _parcel_rate), or ''always'' (full qty every order, e.g. Disposable Spoons).';
        """
    ))


def downgrade() -> None:
    op.execute(sa.text(
        """
        ALTER TABLE public.dish_packaging_map DROP CONSTRAINT IF EXISTS dish_packaging_map_charge_on_check;
        ALTER TABLE public.dish_packaging_map
            ADD CONSTRAINT dish_packaging_map_charge_on_check
            CHECK (charge_on = ANY (ARRAY['parcel', 'dine_in']));
        """
    ))
