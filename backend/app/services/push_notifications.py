"""Push notifications to the staff app (Firebase Cloud Messaging, HTTP v1).

If FCM_PROJECT_ID / FCM_SERVICE_ACCOUNT_JSON are unset -- or a send errors --
this logs instead of raising. A requisition or stock action must never fail
to save because push delivery is unconfigured or unreachable, same
degrade-gracefully rule as app/services/gdrive.py and the WhatsApp/Telegram
daily-brief senders.
"""
import json
import logging
import urllib.error
import urllib.request

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.device_token import DeviceToken

logger = logging.getLogger("push")

_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]


def is_configured() -> bool:
    return bool(settings.FCM_PROJECT_ID and settings.FCM_SERVICE_ACCOUNT_JSON)


def _access_token() -> str | None:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest

    info = json.loads(settings.FCM_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    creds.refresh(GoogleAuthRequest())
    return creds.token


def send_to_users(db: Session, user_ids: list[int], title: str, body: str, data: dict | None = None) -> None:
    """Best-effort push to every registered device of the given users."""
    if not user_ids:
        return
    tokens = [
        t.token for t in db.query(DeviceToken).filter(DeviceToken.user_id.in_(user_ids)).all()
    ]
    if not tokens:
        return

    if not is_configured():
        logger.info("[dry-run] push to users %s: %s / %s", user_ids, title, body)
        return

    try:
        access_token = _access_token()
        url = f"https://fcm.googleapis.com/v1/projects/{settings.FCM_PROJECT_ID}/messages:send"
        for token in tokens:
            message = {
                "message": {
                    "token": token,
                    "notification": {"title": title, "body": body},
                    "data": {k: str(v) for k, v in (data or {}).items()},
                }
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(message).encode(),
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; UTF-8",
                },
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as e:
                logger.warning("FCM send failed (%s): %s", e.code, e.read().decode(errors="replace"))
    except Exception:
        logger.exception("Push notification send failed")
