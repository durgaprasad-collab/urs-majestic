"""Local cache of the Notion task board, for the Live Daily Brief's role
tickets. The deployed app has no NOTION_API_KEY of its own yet, so this
table is the bridge: Claude refreshes it from Notion in an interactive
session (its own Notion connector), the brief reads from here instead of
calling Notion live, and a "done" click writes a local completion that
Claude later pushes back to Notion the same way.
"""

from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notion_task_cache",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("notion_page_id", sa.Text, nullable=False, unique=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("task", sa.Text, nullable=False),
        sa.Column("done_means", sa.Text),
        sa.Column("kill_criterion", sa.Text),
        sa.Column("priority", sa.Text),
        sa.Column("notion_status", sa.Text),  # last-known Status read from Notion
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("local_done", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("local_done_at", sa.DateTime(timezone=True)),
        sa.Column("local_done_by", sa.Text),
        sa.Column("synced_to_notion", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_notion_task_cache_role", "notion_task_cache", ["role"])


def downgrade() -> None:
    op.drop_index("ix_notion_task_cache_role", table_name="notion_task_cache")
    op.drop_table("notion_task_cache")
