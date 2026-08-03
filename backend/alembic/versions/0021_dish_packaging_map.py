"""Add dish_packaging_map + switch packaging cost from a flat global average
to a real per-dish container cost, weighted by the actual parcel rate.

The cost engine previously charged EVERY sold dish the same flat number:
  total packaging spend (across the whole menu) / total units sold (ALL of
  them, dine-in included).
That both overcharges dine-in dishes (which never touch a container) and
undercharges parcel dishes (their real container cost gets diluted across
every dine-in sale too).

This table says which container(s) a menu item uses when it IS parceled, and
how many. The cost engine (separate commit) costs each dish's mapped
container(s) at their real per-piece purchase price, then multiplies by the
parcel rate -- the actual fraction of orders that are parcel/delivery,
computed from existing data: the "Parcel" charge line in item_sales (counter
orders that rang it up) plus every Zomato/Swiggy order (always parcel, no
dine-in option on those channels). No new upload or column needed for that
rate; it's a live query over data already in the DB.

Seed values are the owner's own container choices per category, gathered
2026-08-03: Soups -> Round 250ml; Gravies -> Round 500ml; Rice/Fried
Rice/Noodles -> Rectangle 650ml; all Starters + Tikka -> Rectangle 500ml;
Breads/Tandoori Breads -> Aluminium Foil; each of the 8 Combos mapped
individually to what's actually inside it.

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = """
CREATE TABLE IF NOT EXISTS public.dish_packaging_map (
    id            serial PRIMARY KEY,
    menu_item_id  integer NOT NULL REFERENCES public.menu_items(id) ON DELETE CASCADE,
    ingredient_id integer NOT NULL REFERENCES public.ingredients(id),
    qty           numeric(6,2) NOT NULL DEFAULT 1,
    UNIQUE (menu_item_id, ingredient_id)
);
COMMENT ON TABLE public.dish_packaging_map IS
  'Container(s) a menu item uses when parceled + how many. Costed at real per-piece purchase price, applied at the parcel rate -- see cost_engine._dish_packaging_cost / _parcel_rate.';
"""

# category -> (container ingredient name, qty)
_CATEGORY_SEED = """
INSERT INTO public.dish_packaging_map (menu_item_id, ingredient_id, qty)
SELECT mi.id, ing.id, v.qty
FROM (VALUES
    ('Soups',                'Round Container 250ml',    1),
    ('Gravy-Baby Corn',      'Round Container 500ml',    1),
    ('Gravy-Gobi',           'Round Container 500ml',    1),
    ('Gravy-Mushroom',       'Round Container 500ml',    1),
    ('Gravy-Paneer',         'Round Container 500ml',    1),
    ('Gravy-Veg/Dal',        'Round Container 500ml',    1),
    ('Rice / Briyanis',      'Rectangle Container 650ml',1),
    ('Fried Rice / Noodles', 'Rectangle Container 650ml',1),
    ('Fried Rice & Noodles', 'Rectangle Container 650ml',1),
    ('Starters-65',          'Rectangle Container 500ml',1),
    ('Starters-Chilli',      'Rectangle Container 500ml',1),
    ('Starters-Manchurian',  'Rectangle Container 500ml',1),
    ('Starters-Specials',    'Rectangle Container 500ml',1),
    ('Tikka',                'Rectangle Container 500ml',1),
    ('Breads',               'Aluminium Foil',            1),
    ('Tandoori Breads',      'Aluminium Foil',            1)
) AS v(category, container_name, qty)
JOIN public.menu_items mi ON mi.category = v.category AND mi.is_active AND mi.is_food
JOIN public.ingredients ing ON ing.name = v.container_name
ON CONFLICT (menu_item_id, ingredient_id) DO NOTHING;
"""

# combo name (LIKE-matched, stable across id churn) -> [(container, qty), ...]
_COMBO_SEED = """
INSERT INTO public.dish_packaging_map (menu_item_id, ingredient_id, qty)
SELECT mi.id, ing.id, v.qty
FROM (VALUES
    ('Combo 01%', '3-Compartment Meal Box',    1),
    ('Combo 02%', '3-Compartment Meal Box',    1),
    ('Combo 03%', '2-Compartment Meal Box',    1),
    ('Combo 04%', 'Round Container 250ml',     2),
    ('Combo 05%', '3-Compartment Meal Box',    1),
    ('Combo 06a%','Rectangle Container 650ml', 1),
    ('Combo 06a%','Round Container 250ml',     1),
    ('Combo 06b%','Rectangle Container 650ml', 1),
    ('Combo 06b%','Round Container 250ml',     1),
    ('Combo 06c%','Rectangle Container 650ml', 1),
    ('Combo 06c%','Round Container 250ml',     1)
) AS v(name_pattern, container_name, qty)
JOIN public.menu_items mi ON mi.name LIKE v.name_pattern AND mi.category = 'Combos' AND mi.is_active
JOIN public.ingredients ing ON ing.name = v.container_name
ON CONFLICT (menu_item_id, ingredient_id) DO NOTHING;
"""


def upgrade() -> None:
    op.execute(sa.text(_TABLE))
    op.execute(sa.text(_CATEGORY_SEED))
    op.execute(sa.text(_COMBO_SEED))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS public.dish_packaging_map;"))
