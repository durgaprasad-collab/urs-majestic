"""Cost engine fix: separate overhead/per-order ingredients from recipe
costing, and seed real per-portion gram/ml sizes for light/medium/heavy.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    PgEnum("recipe", "overhead", "per_order", name="cost_role_type").create(bind, checkfirst=True)

    op.add_column(
        "ingredients",
        sa.Column(
            "cost_role",
            PgEnum("recipe", "overhead", "per_order", name="cost_role_type", create_type=False),
            nullable=False,
            server_default="recipe",
        ),
    )
    op.add_column("ingredients", sa.Column("portion_light_g", sa.Numeric(10, 2), nullable=True))
    op.add_column("ingredients", sa.Column("portion_medium_g", sa.Numeric(10, 2), nullable=True))
    op.add_column("ingredients", sa.Column("portion_heavy_g", sa.Numeric(10, 2), nullable=True))

    # Pull overhead / per-order inputs out of recipe costing
    op.execute(sa.text("UPDATE ingredients SET cost_role='overhead' WHERE name ILIKE 'Cooking Gas%'"))
    op.execute(sa.text("UPDATE ingredients SET cost_role='per_order' WHERE name ILIKE 'Packaging%' OR name ILIKE '%Parcel%'"))

    # Seed sane per-portion sizes (grams for solids, ml for liquids).
    # These are STARTING defaults — tune to real recipes over time.
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=40, portion_medium_g=70,  portion_heavy_g=110 WHERE name ILIKE 'Paneer%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=60, portion_medium_g=110, portion_heavy_g=170 WHERE name ILIKE 'Rice%' OR name ILIKE '%Basmati%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=30, portion_medium_g=50,  portion_heavy_g=80  WHERE name ILIKE 'Wheat Flour%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=25, portion_medium_g=45,  portion_heavy_g=75  WHERE name ILIKE 'Maida%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=5,  portion_medium_g=12,  portion_heavy_g=25  WHERE name ILIKE 'Oil%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=5,  portion_medium_g=10,  portion_heavy_g=18  WHERE name ILIKE 'Soya%' OR name ILIKE '%Sauce%' OR name ILIKE 'Vinegar%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=5,  portion_medium_g=12,  portion_heavy_g=22  WHERE name ILIKE 'Corn flour%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=5,  portion_medium_g=12,  portion_heavy_g=22  WHERE name ILIKE 'Butter%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=5,  portion_medium_g=10,  portion_heavy_g=18  WHERE name ILIKE 'Ginger%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=3,  portion_medium_g=6,   portion_heavy_g=12  WHERE name ILIKE 'Green Chilli%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=2,  portion_medium_g=5,   portion_heavy_g=10  WHERE name ILIKE 'Masala%' OR name ILIKE '%Spice%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=50, portion_medium_g=90,  portion_heavy_g=150 WHERE name ILIKE 'Gobi%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=50, portion_medium_g=90,  portion_heavy_g=150 WHERE name ILIKE 'Mushroom%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=40, portion_medium_g=80,  portion_heavy_g=130 WHERE name ILIKE 'Baby Corn%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=20, portion_medium_g=40,  portion_heavy_g=70  WHERE name ILIKE 'Capsicum%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=20, portion_medium_g=40,  portion_heavy_g=70  WHERE name ILIKE '%corn%' AND cost_role='recipe' AND portion_medium_g IS NULL"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=20, portion_medium_g=40,  portion_heavy_g=70  WHERE name ILIKE 'Green peas%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=30, portion_medium_g=60,  portion_heavy_g=100 WHERE name ILIKE '%channa%' OR name ILIKE '%chana%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=30, portion_medium_g=60,  portion_heavy_g=100 WHERE name ILIKE 'Onion%' OR name ILIKE 'Tomato%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=5,  portion_medium_g=10,  portion_heavy_g=20  WHERE name ILIKE 'Cashew%' OR name ILIKE 'Cream%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=20, portion_medium_g=40,  portion_heavy_g=70  WHERE name ILIKE 'Curd%' OR name ILIKE 'Yogurt%' OR name ILIKE 'Raita%'"))
    op.execute(sa.text("UPDATE ingredients SET portion_light_g=2,  portion_medium_g=4,   portion_heavy_g=8   WHERE name ILIKE 'Mint%' OR name ILIKE 'Coriander%'"))

    # Fallback for any recipe ingredient still missing portions
    op.execute(sa.text("""
        UPDATE ingredients
        SET portion_light_g  = COALESCE(portion_light_g, 15),
            portion_medium_g = COALESCE(portion_medium_g, 40),
            portion_heavy_g  = COALESCE(portion_heavy_g, 90)
        WHERE cost_role = 'recipe'
    """))


def downgrade() -> None:
    op.drop_column("ingredients", "portion_heavy_g")
    op.drop_column("ingredients", "portion_medium_g")
    op.drop_column("ingredients", "portion_light_g")
    op.drop_column("ingredients", "cost_role")

    bind = op.get_bind()
    PgEnum(name="cost_role_type").drop(bind, checkfirst=True)
