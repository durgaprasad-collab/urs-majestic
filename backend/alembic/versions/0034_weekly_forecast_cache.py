"""weekly forecast cache -- avoid recomputing the ML/cadence forecast (up to
116 ingredients, each backtested across 3 holdout windows against 4 models)
on every page load and every PDF export. Keyed by category, short TTL
enforced in application code (weekly_ordering.py), not by this table.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_forecast_cache",
        sa.Column("cache_key", sa.Text(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_weekly_forecast_cache_cached_at",
        "weekly_forecast_cache",
        ["cached_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_weekly_forecast_cache_cached_at", table_name="weekly_forecast_cache")
    op.drop_table("weekly_forecast_cache")
