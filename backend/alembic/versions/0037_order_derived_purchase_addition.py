"""Allow 'purchase_addition' events in ingredient_stock_order_derived.

The order-derived comparison model (ingredient_stock_order_derived,
v_stock_model_comparison -- pre-existing, not created by this repo) only
supported 'baseline' and 'order_deduction' events: a pure consumption
ledger with no restocking. Every ingredient that got purchased after its
baseline snapshot drifted increasingly (and misleadingly) negative, since
real purchases were never added back. This widens the CHECK constraint so
app/services/order_derived_stock.py can record purchase additions too.

Revision ID: 0037
Revises: 0036
"""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_OLD_CHECK = "event_type = ANY (ARRAY['baseline'::text, 'order_deduction'::text])"
_NEW_CHECK = "event_type = ANY (ARRAY['baseline'::text, 'order_deduction'::text, 'purchase_addition'::text])"


def upgrade() -> None:
    op.drop_constraint(
        "ingredient_stock_order_derived_event_type_check",
        "ingredient_stock_order_derived", type_="check",
    )
    op.create_check_constraint(
        "ingredient_stock_order_derived_event_type_check",
        "ingredient_stock_order_derived", _NEW_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ingredient_stock_order_derived_event_type_check",
        "ingredient_stock_order_derived", type_="check",
    )
    op.create_check_constraint(
        "ingredient_stock_order_derived_event_type_check",
        "ingredient_stock_order_derived", _OLD_CHECK,
    )
