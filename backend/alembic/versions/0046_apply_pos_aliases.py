"""Apply saved Petpooja name links (pos_aliases) to past item_sales.

The POS import only resolved names from the seed file, so 37 names that
already had a pos_aliases link kept their raw Petpooja label and fell out of
Menu Analysis. The import now reads pos_aliases; this relabels history.
Stock is not touched (those sales were never deducted and stay that way).

Revision ID: 0046
Revises: 0045
"""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE item_sales s SET item_name = m.name
          FROM pos_aliases a JOIN menu_items m ON m.id = a.menu_item_id
         WHERE a.pos_name = s.raw_name AND s.item_name <> m.name
    """)


def downgrade() -> None:
    # Relabelling is data cleanup; the raw name is kept in raw_name.
    pass
