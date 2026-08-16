"""Deactivate the duplicate Cooking Gas fixed-expense rows.

Revision ID: 0030
Revises: 0029
"""

from alembic import op
import sqlalchemy as sa


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE fixed_expenses
               SET active = FALSE
             WHERE name = 'Cooking Gas'
               AND active = TRUE
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE fixed_expenses
               SET active = TRUE
             WHERE name = 'Cooking Gas'
               AND active = FALSE
            """
        )
    )
