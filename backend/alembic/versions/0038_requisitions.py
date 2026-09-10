"""Add requisitions -- staff item requests, decided by an owner.

Standalone table: replaces the WhatsApp "we need onions" message with a
row that has a searchable status and a decision trail (who decided, when,
with what note), for the staff mobile app.

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


_urgency = PgEnum("normal", "urgent", name="requisition_urgency")
_status = PgEnum("pending", "approved", "rejected", name="requisition_status")


def upgrade() -> None:
    _urgency.create(op.get_bind(), checkfirst=True)
    _status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "requisitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("item_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column(
            "unit",
            PgEnum("kg", "g", "l", "ml", "pcs", name="unit_type", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "urgency",
            PgEnum("normal", "urgent", name="requisition_urgency", create_type=False),
            nullable=False, server_default="normal",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            PgEnum("pending", "approved", "rejected", name="requisition_status", create_type=False),
            nullable=False, server_default="pending",
        ),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_requisitions_requested_by_user_id", "requisitions", ["requested_by_user_id"])
    op.create_index("ix_requisitions_status", "requisitions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_requisitions_status", table_name="requisitions")
    op.drop_index("ix_requisitions_requested_by_user_id", table_name="requisitions")
    op.drop_table("requisitions")
    _status.drop(op.get_bind(), checkfirst=True)
    _urgency.drop(op.get_bind(), checkfirst=True)
