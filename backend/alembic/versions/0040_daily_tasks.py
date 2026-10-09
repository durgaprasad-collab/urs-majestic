"""Daily tasks generated from the data after each sales upload.

Replaces the Notion task cache on the Today page: services/task_engine.py
scans sales, channels, menu margins, stock, purchases and data quality, and
writes the day's top issues as tasks for the five delegated roles. One row per
(business_date, rule_key); re-running a day refreshes its open tasks but keeps
the ones already marked done.

Revision ID: 0040
Revises: 0039
"""
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_tasks (
            id            bigserial PRIMARY KEY,
            business_date date        NOT NULL,
            rule_key      text        NOT NULL,
            role          text        NOT NULL CHECK (role IN ('gm', 'coo', 'cro', 'creative', 'bi')),
            priority      text        NOT NULL CHECK (priority IN ('P0', 'P1', 'P2')),
            score         numeric     NOT NULL DEFAULT 0,
            title         text        NOT NULL,
            detail        text        NOT NULL,
            done_means    text,
            href          text,
            status        text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
            done_at       timestamptz,
            done_by       text,
            generated_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_date, rule_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_daily_tasks_date ON daily_tasks (business_date DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_tasks")
