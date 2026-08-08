"""Reconcile purchases entered after the latest manual stock count.

Migration 0025 handles every purchase event going forward. This one-time
backfill applies already-entered purchases that occurred after each item's
latest non-automatic count, so deployment starts from the same rule.
"""

from alembic import op
import sqlalchemy as sa


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        DECLARE
            r record;
        BEGIN
            FOR r IN
                WITH latest_manual_stock AS (
                    SELECT DISTINCT ON (ingredient_id)
                           ingredient_id, counted_at
                      FROM ingredient_stock
                     WHERE COALESCE(note, '') NOT LIKE 'purchase_auto:%'
                     ORDER BY ingredient_id, counted_at DESC, id DESC
                )
                SELECT p.id, p.ingredient_id, p.qty, p.unit::text AS unit,
                       p.entered_by_user_id
                  FROM purchases p
                  LEFT JOIN latest_manual_stock s ON s.ingredient_id = p.ingredient_id
                 WHERE p.deleted_at IS NULL
                   AND p.usage_type::text = 'menu'
                   AND s.counted_at IS NOT NULL
                   AND p.created_at > s.counted_at
                 ORDER BY p.created_at, p.id
            LOOP
                PERFORM append_purchase_stock_delta(
                    r.ingredient_id, r.qty, r.unit, 1,
                    r.entered_by_user_id, r.id
                );
            END LOOP;
        END
        $$
    """))


def downgrade() -> None:
    # Append-only operational balances cannot be safely unwound after staff may
    # have added later counts. The audit note identifies every generated row.
    pass

