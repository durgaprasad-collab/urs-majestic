"""One grams value per recipe line.

ingredient_dish_map had two per-dish gram columns: grams_override (read by
food-cost views and stock deduction) and portion_override_g (read only by the
cost engine behind Menu Analysis). Each part of the system ignored the
other's weights. Move every portion_override_g into grams_override where that
is empty, and clear it where both agree. Lines where they disagree are left
with both values so the owner picks one on the Recipes page.
Then every remaining line gets its own grams: the owner's rule is that an
ingredient's quantity differs in every recipe, so the shared light/medium/
heavy tiers stop being used. Lines still on a tier get that tier's grams
copied in once, marked grams_source = 'estimate' until confirmed per dish;
weighed lines are 'weighed'.
v_dish_recipe_cost falls back to portion_override_g the same way, so every
reader uses grams_override -> portion_override_g (-> tier only if both empty).

Revision ID: 0047
Revises: 0046
"""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ingredient_dish_map ADD COLUMN IF NOT EXISTS grams_source text")
    op.execute("""
        UPDATE ingredient_dish_map SET grams_source = 'weighed'
         WHERE grams_override IS NOT NULL OR portion_override_g IS NOT NULL
    """)
    op.execute("""
        UPDATE ingredient_dish_map SET grams_override = portion_override_g, portion_override_g = NULL
         WHERE grams_override IS NULL AND portion_override_g IS NOT NULL
    """)
    op.execute("""
        UPDATE ingredient_dish_map SET portion_override_g = NULL
         WHERE portion_override_g IS NOT NULL AND portion_override_g = grams_override
    """)
    op.execute("""
        UPDATE ingredient_dish_map m SET grams_source = 'estimate',
               grams_override = CASE m.intensity::text WHEN 'light' THEN i.portion_light_g
                                                       WHEN 'medium' THEN i.portion_medium_g
                                                       ELSE i.portion_heavy_g END
          FROM ingredients i
         WHERE i.id = m.ingredient_id AND m.grams_override IS NULL AND m.portion_override_g IS NULL
    """)
    op.execute("""
        CREATE OR REPLACE VIEW v_dish_recipe_cost AS
        WITH lines AS (
             SELECT m.menu_item_id, m.ingredient_id,
                    COALESCE(m.grams_override, m.portion_override_g,
                        CASE (m.intensity)::text
                            WHEN 'light'::text THEN i.portion_light_g
                            WHEN 'medium'::text THEN i.portion_medium_g
                            ELSE i.portion_heavy_g
                        END) AS portion_g,
                    (m.grams_source = 'weighed') AS is_overridden,
                    c.base_unit, c.cost_per_base_unit
               FROM ingredient_dish_map m
               JOIN ingredients i ON i.id = m.ingredient_id
               JOIN v_ingredient_cost c ON c.ingredient_id = m.ingredient_id
              WHERE (i.cost_role)::text = 'recipe'::text
        )
        SELECT menu_item_id,
               round(sum(CASE WHEN base_unit = ANY (ARRAY['gml'::text, 'pc'::text]) THEN portion_g * cost_per_base_unit ELSE 0::numeric END), 2) AS recipe_cost,
               count(*) AS n_ingredients,
               count(*) FILTER (WHERE cost_per_base_unit IS NULL OR portion_g IS NULL OR base_unit <> ALL (ARRAY['gml'::text, 'pc'::text])) AS n_unpriced,
               count(*) FILTER (WHERE is_overridden) AS n_measured
          FROM lines
         GROUP BY menu_item_id
    """)


def downgrade() -> None:
    # Data merge is not reversed; the view change is compatible.
    pass
