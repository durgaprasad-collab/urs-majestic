"""Gravy readings: how much gravy was left in the vessel on a given day.

The Gravy page estimates daily use from dish sales; a reading ("9.5 kg left on
Fri") re-anchors that estimate and calibrates grams used per dish for the next
batch. Batch 5 (26-Oct) is seeded with the owner's note: 3 kg left on 3-Nov.

Revision ID: 0059
Revises: 0058
"""
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS gravy_readings (
            id serial PRIMARY KEY,
            batch_id integer NOT NULL REFERENCES gravy_batches(id) ON DELETE CASCADE,
            reading_date date NOT NULL,
            kg_left numeric(6,2) NOT NULL CHECK (kg_left >= 0),
            note text,
            created_by integer,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gravy_readings_batch ON gravy_readings (batch_id, reading_date)")
    op.execute("""
        INSERT INTO gravy_readings (batch_id, reading_date, kg_left, note)
        SELECT id, DATE '2026-11-03', 3.0, 'Owner: 3 kg left on 3-Nov (seeded from batch notes)'
          FROM gravy_batches WHERE batch_date = DATE '2026-10-26'
           AND NOT EXISTS (SELECT 1 FROM gravy_readings r WHERE r.batch_id = gravy_batches.id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gravy_readings")
