"""Business Settings module: fixed_expenses + business_settings (append-only
history of the tunable financial assumptions).

Additive, guarded (CREATE TABLE IF NOT EXISTS), so it applies cleanly to the
already-migrated production DB or a fresh one. Seeding of the owner's current
expenses / default assumptions is done separately (scripts/seed_business.py) so
this migration stays purely structural and idempotent.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS fixed_expenses (
            id              serial PRIMARY KEY,
            name            text NOT NULL,
            category        text NOT NULL DEFAULT 'Other',
            amount          numeric(12,2) NOT NULL,
            frequency       text NOT NULL DEFAULT 'monthly',
            active          boolean NOT NULL DEFAULT true,
            effective_from  date NOT NULL DEFAULT current_date,
            effective_to    date,
            notes           text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fixed_expenses_frequency_check
                CHECK (frequency = ANY (ARRAY['monthly','quarterly','half_yearly','yearly'])),
            CONSTRAINT fixed_expenses_amount_check CHECK (amount >= 0)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_fixed_expenses_active ON fixed_expenses (active)"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS business_settings (
            id              serial PRIMARY KEY,
            setting_key     text NOT NULL,
            value           numeric(12,2) NOT NULL,
            effective_from  date NOT NULL DEFAULT current_date,
            note            text,
            created_by      text,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_business_settings_key ON business_settings (setting_key)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS business_settings"))
    op.execute(sa.text("DROP TABLE IF EXISTS fixed_expenses"))
