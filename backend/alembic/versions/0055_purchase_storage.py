"""Record purchase storage without guessing historical locations."""
from alembic import op
revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE purchases ADD COLUMN storage_location text CHECK (storage_location IN ('home', 'restaurant'))")

def downgrade():
    op.execute("ALTER TABLE purchases DROP COLUMN storage_location")
