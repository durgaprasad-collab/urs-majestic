"""Seven-day inventory forecasts and planned delivery receiving.

Revision ID: 0028
Revises: 0027
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingredients",
        sa.Column("order_increment_qty", sa.Numeric(12, 3), nullable=True),
    )

    op.create_table(
        "weekly_inventory_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("horizon_start", sa.Date, nullable=False),
        sa.Column("horizon_end", sa.Date, nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_by", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("horizon_end >= horizon_start", name="weekly_order_valid_horizon"),
        sa.CheckConstraint(
            "status IN ('draft','approved','partially_received','received','cancelled')",
            name="weekly_order_valid_status",
        ),
    )

    op.create_table(
        "weekly_inventory_order_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("weekly_inventory_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_id", sa.Integer, sa.ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit", postgresql.ENUM(name="unit_type", create_type=False), nullable=False),
        sa.Column("historical_qty", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("historical_spend", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("spend_contribution_pct", sa.Numeric(8, 4)),
        sa.Column("forecast_value_contribution_pct", sa.Numeric(8, 4)),
        sa.Column("forecast_qty", sa.Numeric(14, 4)),
        sa.Column("forecast_low", sa.Numeric(14, 4)),
        sa.Column("forecast_high", sa.Numeric(14, 4)),
        sa.Column("safety_qty", sa.Numeric(14, 4)),
        sa.Column("stock_qty", sa.Numeric(14, 4)),
        sa.Column("inbound_qty", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("suggested_qty", sa.Numeric(14, 4)),
        sa.Column("approved_qty", sa.Numeric(14, 4)),
        sa.Column("order_increment_qty", sa.Numeric(12, 3)),
        sa.Column("recent_unit_cost", sa.Numeric(14, 4)),
        sa.Column("model_name", sa.String(50)),
        sa.Column("backtest_wape", sa.Numeric(10, 6)),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("needs_input", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("input_reason", sa.Text),
        sa.Column("daily_forecast", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("diagnostics", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("order_id", "ingredient_id", name="uq_weekly_order_ingredient"),
        sa.CheckConstraint("approved_qty IS NULL OR approved_qty >= 0", name="weekly_line_nonnegative_approved"),
    )
    op.create_index("ix_weekly_inventory_order_lines_order", "weekly_inventory_order_lines", ["order_id"])

    op.create_table(
        "weekly_inventory_deliveries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("line_id", sa.Integer, sa.ForeignKey("weekly_inventory_order_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delivery_date", sa.Date, nullable=False),
        sa.Column("planned_qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("received_qty", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.CheckConstraint("planned_qty >= 0 AND received_qty >= 0", name="weekly_delivery_nonnegative"),
        sa.CheckConstraint("status IN ('planned','partial','received','cancelled')", name="weekly_delivery_valid_status"),
    )
    op.create_index("ix_weekly_inventory_deliveries_line", "weekly_inventory_deliveries", ["line_id"])
    op.create_index("ix_weekly_inventory_deliveries_date", "weekly_inventory_deliveries", ["delivery_date"])

    op.create_table(
        "weekly_inventory_receipts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("delivery_id", sa.Integer, sa.ForeignKey("weekly_inventory_deliveries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("purchase_id", sa.Integer, sa.ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("received_by", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("qty > 0 AND total_price >= 0", name="weekly_receipt_positive_qty"),
    )
    op.create_index("ix_weekly_inventory_receipts_delivery", "weekly_inventory_receipts", ["delivery_id"])


def downgrade() -> None:
    op.drop_index("ix_weekly_inventory_receipts_delivery", table_name="weekly_inventory_receipts")
    op.drop_table("weekly_inventory_receipts")
    op.drop_index("ix_weekly_inventory_deliveries_date", table_name="weekly_inventory_deliveries")
    op.drop_index("ix_weekly_inventory_deliveries_line", table_name="weekly_inventory_deliveries")
    op.drop_table("weekly_inventory_deliveries")
    op.drop_index("ix_weekly_inventory_order_lines_order", table_name="weekly_inventory_order_lines")
    op.drop_table("weekly_inventory_order_lines")
    op.drop_table("weekly_inventory_orders")
    op.drop_column("ingredients", "order_increment_qty")
