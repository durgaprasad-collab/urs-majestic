"""Create whatsapp_webhook_events -- the delivery/status log the webhook writes.

app/api/routes/whatsapp_webhooks.py inserts one row per Meta callback (a
delivery/read/failed status update, or an inbound customer reply) so that a
silently-failed send can be diagnosed after the fact. The route already exists
in production but the table did not, so every POST was rolling back and the
handler was returning 200 with nothing persisted. This creates the table the
route's INSERTs target.

Idempotent (IF NOT EXISTS) because dev == prod on Supabase and the table may
have been created directly in the dashboard before this migration is stamped.

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Columns mirror exactly what receive_webhook() inserts. Only event_type and
# raw_payload are ever guaranteed present (status events fill the rest; inbound
# messages leave status/error_*/category NULL), so everything else is nullable.
_CREATE = """
CREATE TABLE IF NOT EXISTS public.whatsapp_webhook_events (
    id                    bigserial PRIMARY KEY,
    received_at           timestamptz NOT NULL DEFAULT now(),
    event_type            text        NOT NULL,   -- 'status' | 'inbound_message'
    wa_message_id         text,                   -- Meta wamid (status target or inbound id)
    recipient_wa_id       text,                   -- recipient (status) or sender (inbound)
    status                text,                   -- sent | delivered | read | failed
    error_code            integer,                -- Meta error code on a failed send
    error_title           text,
    error_message         text,
    conversation_category text,                   -- utility | marketing | ... (pricing bucket)
    raw_payload           text        NOT NULL    -- verbatim event JSON, for after-the-fact triage
);

-- Look-ups are "what happened to wamid X" and "what came in recently".
CREATE INDEX IF NOT EXISTS ix_wwe_wa_message_id ON public.whatsapp_webhook_events (wa_message_id);
CREATE INDEX IF NOT EXISTS ix_wwe_received_at   ON public.whatsapp_webhook_events (received_at DESC);

COMMENT ON TABLE public.whatsapp_webhook_events IS
  'Raw log of every WhatsApp Cloud API webhook callback (delivery status + inbound replies). Written by /webhooks/whatsapp; the only source of delivered/failed truth since the send API''s 200 only means "queued".';
"""


def upgrade() -> None:
    op.execute(sa.text(_CREATE))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS public.whatsapp_webhook_events;"))
