"""Add catering_orders and catering_order_items -- standalone tables for
catering bookings, not a discriminator on the existing orders/order_items
(which have no payment_status/advance/balance concept and use a different
customer_id-FK, quantity*unit_price line-item shape).

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


_payment_status = PgEnum(
    "pending", "partial", "paid",
    name="catering_payment_status",
)
_order_status = PgEnum(
    "confirmed", "in_prep", "delivered", "cancelled",
    name="catering_order_status",
)


def upgrade() -> None:
    _payment_status.create(op.get_bind(), checkfirst=True)
    _order_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "catering_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("customer_phone", sa.String(20), nullable=False),
        sa.Column("order_taken_date", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("delivery_time", sa.Time(), nullable=False),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
        sa.Column("discount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("advance_paid", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("balance_due", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "payment_status",
            PgEnum("pending", "partial", "paid", name="catering_payment_status", create_type=False),
            nullable=False, server_default="pending",
        ),
        sa.Column(
            "status",
            PgEnum("confirmed", "in_prep", "delivered", "cancelled", name="catering_order_status", create_type=False),
            nullable=False, server_default="confirmed",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_catering_orders_customer_phone", "catering_orders", ["customer_phone"])

    op.create_table(
        "catering_order_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "catering_order_id", sa.Integer(),
            sa.ForeignKey("catering_orders.id"), nullable=False,
        ),
        sa.Column("item_name", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("catering_order_items")
    op.drop_index("ix_catering_orders_customer_phone", table_name="catering_orders")
    op.drop_table("catering_orders")
    _order_status.drop(op.get_bind(), checkfirst=True)
    _payment_status.drop(op.get_bind(), checkfirst=True)
