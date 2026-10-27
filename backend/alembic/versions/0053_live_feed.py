"""Live stock feed: reads of Petpooja's live Item Report + helper codes.

The Chrome helper on the restaurant PC reads Petpooja's Item Report every ~10
minutes and posts the rows here (live_sales_reads). They are only ever *shown*
(Live stock tab); nothing is written to ingredient_stock until the normal
nightly upload. live_feed_tokens holds the helper's codes (hashed).

Revision ID: 0053
Revises: 0052
"""
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS live_feed_tokens (
            id serial PRIMARY KEY,
            token_hash text NOT NULL UNIQUE,
            label text NOT NULL DEFAULT 'Chrome helper',
            created_by_user_id integer REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            revoked_at timestamptz
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS live_sales_reads (
            id bigserial PRIMARY KEY,
            business_date date NOT NULL,
            read_at timestamptz NOT NULL DEFAULT now(),
            client_read_at timestamptz,
            rows jsonb NOT NULL,
            dishes numeric(12, 2) NOT NULL DEFAULT 0,
            amount numeric(12, 2) NOT NULL DEFAULT 0,
            token_id integer REFERENCES live_feed_tokens(id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_live_sales_reads_day ON live_sales_reads (business_date, read_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_sales_reads")
    op.execute("DROP TABLE IF EXISTS live_feed_tokens")
