"""Add ingredient_dish_map.portion_override_g — an escape hatch for when the
same ingredient plays a genuinely different-sized role in one dish than its
shared light/medium/heavy tiers assume.

Surfaced by Manchow Soup: its fried-noodle garnish was mapped to "Noodles
(dried)" at intensity='light', which is 100g — but that 100g tier is shared
by the 6 real noodle main courses (Veg Noodles, Gobi Noodles, etc.), so it's
correctly calibrated for THEM, not for a soup topping. Lowering the shared
tier would quietly shrink those 6 dishes' real portions too. A per-mapping
override sidesteps that without touching the ingredient's tiers or splitting
one physical product into two ingredient rows (which would fragment the
purchase ledger — see purchase-ledger-conventions memory).

NULL (all existing rows) means "use the intensity tier", so this is a
zero-behavior-change addition until a row explicitly sets it.

Revision ID: 0022
Revises: 0021
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE public.ingredient_dish_map "
        "ADD COLUMN IF NOT EXISTS portion_override_g numeric(10,2) NULL;"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN public.ingredient_dish_map.portion_override_g IS "
        "'Explicit grams for this one dish, bypassing the ingredient''s shared "
        "light/medium/heavy tiers. NULL = use intensity tier (the default).';"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE public.ingredient_dish_map DROP COLUMN IF EXISTS portion_override_g;"
    ))
