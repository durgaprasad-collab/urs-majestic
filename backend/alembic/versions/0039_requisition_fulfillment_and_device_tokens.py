"""Requisition -> purchase fulfillment tracking, plus device tokens for push.

Adds:
  - requisitions.ingredient_id: best-effort link to the ingredient catalog,
    resolved at creation time from the free-text item name. Lets an approved
    requisition be cross-referenced against the purchases table.
  - requisitions.matched_purchase_id: set once a real purchase shows up for
    an approved requisition -- the "ordered vs received" close-the-loop.
  - requisition_status gains 'fulfilled' (approved -> fulfilled once matched;
    anything approved-but-unmatched stays visible in the app).
  - device_tokens: one row per (user, device) for push notifications --
    stock reminders to staff, requisition alerts to owners.

Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New enum values can't be used in the same transaction they're added in,
    # but this migration only adds the value -- nothing here inserts a
    # 'fulfilled' row -- so it's safe inside alembic's transactional DDL.
    op.execute("ALTER TYPE requisition_status ADD VALUE IF NOT EXISTS 'fulfilled'")

    op.add_column(
        "requisitions",
        sa.Column("ingredient_id", sa.Integer(), sa.ForeignKey("ingredients.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "requisitions",
        sa.Column("matched_purchase_id", sa.Integer(), sa.ForeignKey("purchases.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_requisitions_ingredient_id", "requisitions", ["ingredient_id"])

    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(255), nullable=False, unique=True),
        sa.Column("platform", sa.String(20), nullable=False, server_default="android"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_device_tokens_user_id", table_name="device_tokens")
    op.drop_table("device_tokens")
    op.drop_index("ix_requisitions_ingredient_id", table_name="requisitions")
    op.drop_column("requisitions", "matched_purchase_id")
    op.drop_column("requisitions", "ingredient_id")
    # Postgres can't drop a single enum value -- leaving 'fulfilled' defined
    # is harmless (downgrade only needs to undo the columns/table above).
