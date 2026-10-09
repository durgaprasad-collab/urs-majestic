"""Catering: quotes, plates, and order lines tied to menu dishes.

* catering_order_status gains 'quote' (before 'confirmed').
* catering_orders: plates, notes, created_by, buy_added_at.
* catering_order_items: menu_item_id (NULL for a custom item), menu_price
  and rate (catering price per unit; amount = quantity x rate).
The two orders entered by hand in Aug/Sep get menu_item_id where the line
clearly is a menu dish, and rate = amount / quantity.

Revision ID: 0045
Revises: 0044
"""
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE can't run inside a transaction block on older Postgres.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE catering_order_status ADD VALUE IF NOT EXISTS 'quote' BEFORE 'confirmed'")
    op.execute("""
        ALTER TABLE catering_orders
          ADD COLUMN IF NOT EXISTS plates integer,
          ADD COLUMN IF NOT EXISTS notes text,
          ADD COLUMN IF NOT EXISTS created_by integer REFERENCES users(id),
          ADD COLUMN IF NOT EXISTS buy_added_at timestamptz
    """)
    op.execute("""
        ALTER TABLE catering_order_items
          ADD COLUMN IF NOT EXISTS menu_item_id integer REFERENCES menu_items(id),
          ADD COLUMN IF NOT EXISTS menu_price numeric(10,2),
          ADD COLUMN IF NOT EXISTS rate numeric(10,2)
    """)
    op.execute("UPDATE catering_order_items SET rate = round(amount / NULLIF(quantity, 0), 2) WHERE rate IS NULL")
    for old, menu in (("Chapati & Channa (Set)", "Chappati (2) With Channa"),
                      ("Chilli Garlic Fried Rice", "Chilli Garlic Fried Rice"),
                      ("Veg Fried Rice", "Veg Fried Rice"),
                      ("Gobi Manchurian", "Gobi Manchurian (Starter)"),
                      ("Chappati (2), plain", "Phulka (2 Nos)")):
        op.execute(f"""
            UPDATE catering_order_items ci SET menu_item_id = m.id, menu_price = m.price
              FROM menu_items m
             WHERE ci.menu_item_id IS NULL AND ci.item_name = '{old}' AND m.name = '{menu}'
        """)


def downgrade() -> None:
    op.execute("ALTER TABLE catering_order_items DROP COLUMN IF EXISTS rate, DROP COLUMN IF EXISTS menu_price, "
               "DROP COLUMN IF EXISTS menu_item_id")
    op.execute("ALTER TABLE catering_orders DROP COLUMN IF EXISTS buy_added_at, DROP COLUMN IF EXISTS created_by, "
               "DROP COLUMN IF EXISTS notes, DROP COLUMN IF EXISTS plates")
    # Enum values can't be dropped; 'quote' stays harmlessly.
