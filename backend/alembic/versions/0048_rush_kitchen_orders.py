"""Rush slips: kitchen station + cook time per dish, and kitchen orders.

The captain enters each order on the staff app (Rush tab); the app prints
one slip per station with start-by times worked back from a 12-minute
promise (design/mockups/17-rush-slips.html). Stations: T tandoor, W wok,
G gravy + rice, F fryer/tawa. Combos have no station; they split into their
component dishes (combo_components). Defaults below come from the menu
category; the owner can change any dish later.

Revision ID: 0048
Revises: 0047
"""
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS kitchen_station text")
    op.execute("ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS cook_minutes integer")
    op.execute("""
        UPDATE menu_items SET kitchen_station = CASE
            WHEN NOT is_food THEN NULL
            WHEN category = 'Combos' THEN NULL
            WHEN name = 'Chilli Parotta' THEN 'W'
            WHEN category IN ('Tandoori Breads', 'Tikka') OR name ILIKE '%shawarma%' THEN 'T'
            WHEN category IN ('Fried Rice / Noodles', 'Fried Rice & Noodles', 'Starters-Manchurian', 'Starters-Chilli', 'Soups') THEN 'W'
            WHEN category LIKE 'Gravy-%' OR category = 'Rice / Briyanis' THEN 'G'
            WHEN category IN ('Breads', 'Starters-65', 'Starters-Specials') THEN 'F'
            ELSE 'G' END
         WHERE kitchen_station IS NULL
    """)
    op.execute("""
        UPDATE menu_items SET cook_minutes = CASE
            WHEN kitchen_station IS NULL THEN NULL
            WHEN name ILIKE 'Roti%' OR name ILIKE 'Butter Roti%' THEN 2
            WHEN name ILIKE '%kulcha%' THEN 4
            WHEN category = 'Tandoori Breads' THEN 3
            WHEN category = 'Tikka' THEN 8
            WHEN name ILIKE '%shawarma%' THEN 6
            WHEN category = 'Soups' THEN 4
            WHEN kitchen_station = 'W' THEN 5
            WHEN name = 'Jeera Rice' THEN 1
            WHEN category = 'Rice / Briyanis' THEN 2
            WHEN kitchen_station = 'G' THEN 3
            WHEN name ILIKE '%phulka%' OR name ILIKE '%batura%' THEN 3
            WHEN name ILIKE '%paratha%' OR category = 'Starters-Specials' THEN 5
            ELSE 4 END
         WHERE cook_minutes IS NULL
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS kitchen_orders (
            id serial PRIMARY KEY,
            order_type text NOT NULL,
            ref text,
            status text NOT NULL DEFAULT 'open',
            created_at timestamptz NOT NULL DEFAULT now(),
            created_by integer REFERENCES users(id),
            packed_at timestamptz
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS kitchen_order_items (
            id serial PRIMARY KEY,
            order_id integer NOT NULL REFERENCES kitchen_orders(id) ON DELETE CASCADE,
            menu_item_id integer NOT NULL REFERENCES menu_items(id),
            qty integer NOT NULL CHECK (qty > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_kitchen_orders_open ON kitchen_orders (status, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kitchen_order_items")
    op.execute("DROP TABLE IF EXISTS kitchen_orders")
    op.execute("ALTER TABLE menu_items DROP COLUMN IF EXISTS cook_minutes")
    op.execute("ALTER TABLE menu_items DROP COLUMN IF EXISTS kitchen_station")
