"""Requisition aliases: what staff type -> which item, learned from the owner.

When the owner picks the item for a typed request on Buy ("Chini" -> Sugar),
the typed name is saved here, so the next request with the same wording is
matched on entry and never lands unmatched again.

Revision ID: 0052
Revises: 0051
"""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS requisition_aliases (
            alias text PRIMARY KEY,
            ingredient_id integer NOT NULL REFERENCES ingredients(id),
            created_by_user_id integer REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT requisition_aliases_alias_lower CHECK (alias = lower(btrim(alias)) AND alias <> '')
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS requisition_aliases")
