"""Receipt archival: purchase_receipts + purchases.purchase_receipt_id.

A receipt/screenshot uploaded on /purchases/new is OCR'd, parsed into candidate
line items the owner reviews, and (on confirm) turned into purchase rows. The
image itself is archived to Google Drive. This table records each uploaded
receipt (its Drive file + the raw OCR text) so every purchase created from it can
point back to its source -- the audit trail the purchases ledger already values.

Revision ID: 0020
Revises: 0019
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UP = """
CREATE TABLE IF NOT EXISTS public.purchase_receipts (
    id                bigserial PRIMARY KEY,
    drive_file_id     text        NULL,       -- Google Drive file id (NULL if archival was skipped)
    drive_link        text        NULL,       -- webViewLink to the archived image
    original_filename text        NULL,
    stored_filename   text        NULL,       -- snake_case(uploader)_date[.n].ext
    content_type      text        NULL,
    ocr_text          text        NULL,       -- raw tesseract output, for later re-parsing / audit
    uploaded_by       integer     NULL REFERENCES public.users(id),
    uploaded_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.purchase_receipts IS
  'One row per uploaded purchase receipt/screenshot. purchases.purchase_receipt_id links each created purchase back to its source image (archived on Drive).';

ALTER TABLE public.purchases
  ADD COLUMN IF NOT EXISTS purchase_receipt_id integer NULL REFERENCES public.purchase_receipts(id);

CREATE INDEX IF NOT EXISTS ix_purchases_receipt ON public.purchases (purchase_receipt_id);
"""


def upgrade() -> None:
    op.execute(sa.text(_UP))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE public.purchases DROP COLUMN IF EXISTS purchase_receipt_id;"))
    op.execute(sa.text("DROP TABLE IF EXISTS public.purchase_receipts;"))
