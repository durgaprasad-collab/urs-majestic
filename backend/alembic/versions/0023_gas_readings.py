"""Add gas_readings -- measured LPG cylinder weight log.

Cooking gas has always been costed as a flat OVERHEAD_PER_DISH guess
(cost_engine.py) because nothing measured actual consumption. The owner is
now weighing cylinders (gross weight; tare is fixed per cylinder, currently
20kg) to get real kg-of-gas-used over time, the same "replace an assumption
with a measurement" move as the Stock Log and the packaging-cost rewrite.

Each reading belongs to a role ('in_use' or 'spare') since only one cylinder
is actually being drawn from at a time -- the spare's weight doesn't change
until it's swapped in. `is_new_cylinder` marks a reading as the start of a
fresh fill/swap, so consumption is computed only between consecutive
readings of the SAME physical cylinder, never across a swap.

This is data collection only -- nothing in the cost engine reads this table
yet. Once a real trend exists, a follow-up will derive gas cost per dish
from it, the same way _parcel_rate() replaced a flat packaging average.

Revision ID: 0023
Revises: 0022
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = """
CREATE TABLE IF NOT EXISTS public.gas_readings (
    id               bigserial PRIMARY KEY,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    cylinder_role    text        NOT NULL CHECK (cylinder_role IN ('in_use', 'spare')),
    gross_kg         numeric     NOT NULL CHECK (gross_kg >= 0),
    tare_kg          numeric     NOT NULL DEFAULT 20,
    is_new_cylinder  boolean     NOT NULL DEFAULT false,
    recorded_by      integer     NULL REFERENCES public.users(id),
    note             text        NULL
);
CREATE INDEX IF NOT EXISTS ix_gas_readings_role_time
    ON public.gas_readings (cylinder_role, recorded_at DESC);

COMMENT ON TABLE public.gas_readings IS
  'Owner-entered LPG cylinder weight readings (gross - tare = kg of gas). cylinder_role tracks the in-use vs spare cylinder separately since only in_use is being consumed. is_new_cylinder=true breaks the consumption-delta chain (a swap/refill happened) so a fresh full cylinder is never read as "gas appeared".';
"""


def upgrade() -> None:
    op.execute(sa.text(_TABLE))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS public.gas_readings;"))
