"""Gas readings per cylinder: Tandoor and Kitchen, weighed nightly in the app.

Adds gas_readings.cylinder ('tandoor' | 'kitchen'). The 53 readings logged
3 Aug - 18 Sep 2026 were a single in-use cylinder; they're kept as
'kitchen' history. cylinder_role stays for those old rows ('in_use'/'spare');
new nightly readings are always 'in_use'.

Revision ID: 0043
Revises: 0042
"""
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gas_readings ADD COLUMN IF NOT EXISTS cylinder text")
    op.execute("UPDATE gas_readings SET cylinder = 'kitchen' WHERE cylinder IS NULL")
    op.execute("ALTER TABLE gas_readings ALTER COLUMN cylinder SET NOT NULL")
    op.execute("ALTER TABLE gas_readings ALTER COLUMN cylinder SET DEFAULT 'kitchen'")
    op.execute("""
        ALTER TABLE gas_readings ADD CONSTRAINT ck_gas_readings_cylinder
        CHECK (cylinder IN ('tandoor', 'kitchen'))
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gas_readings_cylinder_time ON gas_readings (cylinder, recorded_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_gas_readings_cylinder_time")
    op.execute("ALTER TABLE gas_readings DROP CONSTRAINT IF EXISTS ck_gas_readings_cylinder")
    op.execute("ALTER TABLE gas_readings DROP COLUMN IF EXISTS cylinder")
