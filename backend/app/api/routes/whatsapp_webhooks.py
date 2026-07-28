"""WhatsApp Cloud API webhook receiver.

Meta's synchronous response to POST /messages ("accepted") only means the
request was well-formed and queued -- it says nothing about whether the
message was actually delivered. The only way to know delivered/read/failed
status, and the *exact* Meta error code when a send silently fails, is via
this webhook. Every event is logged verbatim to whatsapp_webhook_events so
a failure can be diagnosed after the fact without reproducing it live.

SETUP (one-time, in Meta for Developers -> your app -> WhatsApp -> Configuration):
  Callback URL   : https://admin.ursmajestic.com/webhooks/whatsapp
  Verify token   : must match WHATSAPP_WEBHOOK_VERIFY_TOKEN in .env
  Subscribe to   : "messages" field

Meta calls GET once, when you save the config, to verify the endpoint
(challenge/response). Meta calls POST every time a message's status changes
or a customer replies.
"""
import json
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("whatsapp_webhook")

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp-webhook"])


@router.get("")
def verify_webhook(request: Request):
    """Meta's one-time handshake. Echo back hub.challenge if the token matches."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")

    logger.warning(
        "WhatsApp webhook verification failed: mode=%s token_match=%s",
        mode, token == settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN,
    )
    return Response(status_code=403)


@router.post("")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    """Log every status update and inbound message. Always return 200 fast --
    Meta disables a webhook endpoint that times out or errors repeatedly."""
    body = await request.json()
    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # Delivery/read/failed status callbacks -- this is what we're missing today.
                for status_event in value.get("statuses", []):
                    error = (status_event.get("errors") or [{}])[0]
                    db.execute(
                        text(
                            "INSERT INTO whatsapp_webhook_events "
                            "(event_type, wa_message_id, recipient_wa_id, status, "
                            " error_code, error_title, error_message, conversation_category, raw_payload) "
                            "VALUES ('status', :wamid, :recipient, :status, "
                            " :error_code, :error_title, :error_message, :category, :raw)"
                        ),
                        {
                            "wamid": status_event.get("id"),
                            "recipient": status_event.get("recipient_id"),
                            "status": status_event.get("status"),
                            "error_code": error.get("code"),
                            "error_title": error.get("title"),
                            "error_message": error.get("message") or (error.get("error_data") or {}).get("details"),
                            "category": ((status_event.get("conversation") or {}).get("origin") or {}).get("type"),
                            "raw": json.dumps(status_event),
                        },
                    )

                # Inbound replies from customers (not today's problem, but cheap to capture).
                for message in value.get("messages", []):
                    db.execute(
                        text(
                            "INSERT INTO whatsapp_webhook_events "
                            "(event_type, wa_message_id, recipient_wa_id, raw_payload) "
                            "VALUES ('inbound_message', :wamid, :sender, :raw)"
                        ),
                        {
                            "wamid": message.get("id"),
                            "sender": message.get("from"),
                            "raw": json.dumps(message),
                        },
                    )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to process WhatsApp webhook payload: %s", body)

    # Always 200, even on internal errors above -- a non-200 or timeout makes
    # Meta back off and eventually disable the webhook subscription.
    return Response(status_code=200)
