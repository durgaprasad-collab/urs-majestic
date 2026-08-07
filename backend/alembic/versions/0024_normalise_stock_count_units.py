"""Normalise saved stock counts to the forecast consumption unit.

Stock Log historically saved counts in the ingredient catalogue unit, while
the reorder view consumes the dominant purchase unit. A kg count divided by a
g/day rate understated cover by 1000x. Runtime writes now convert before
insert; this migration repairs existing compatible weight/volume rows.
"""

from alembic import op
import sqlalchemy as sa


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE ingredient_stock s
           SET on_hand_qty = CASE
                 WHEN s.unit = 'kg' AND v.unit::text = 'g' THEN s.on_hand_qty * 1000
                 WHEN s.unit = 'g' AND v.unit::text = 'kg' THEN s.on_hand_qty / 1000
                 WHEN s.unit = 'l' AND v.unit::text = 'ml' THEN s.on_hand_qty * 1000
                 WHEN s.unit = 'ml' AND v.unit::text = 'l' THEN s.on_hand_qty / 1000
                 ELSE s.on_hand_qty
               END,
               unit = v.unit::text
          FROM v_ingredient_reorder_forecast v
         WHERE v.ingredient_id = s.ingredient_id
           AND (s.unit, v.unit::text) IN (
               ('kg', 'g'), ('g', 'kg'), ('l', 'ml'), ('ml', 'l')
           )
    """))


def downgrade() -> None:
    # Data correction is intentionally irreversible: the original count unit
    # cannot be inferred after normalisation without risking valid later rows.
    pass
