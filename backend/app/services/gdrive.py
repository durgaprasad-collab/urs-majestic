"""Google Drive archival of uploaded receipts (optional).

If GOOGLE_SERVICE_ACCOUNT_JSON / GDRIVE_RECEIPTS_FOLDER_ID are unset -- or the
upload errors -- this returns (None, None) instead of raising. A purchase must
never fail to save because Drive is unconfigured or unreachable. Google client
imports are local so the module loads even where those packages are absent.
"""
import io
import json
import logging

from app.core.config import settings

logger = logging.getLogger("gdrive")

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def is_configured() -> bool:
    return bool(settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GDRIVE_RECEIPTS_FOLDER_ID)


def upload_receipt(data: bytes, filename: str, content_type: str) -> tuple[str | None, str | None]:
    """Upload bytes to the receipts folder. Returns (file_id, web_view_link),
    or (None, None) if archival is unconfigured or fails (logged, not raised)."""
    if not is_configured():
        return None, None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        media = MediaIoBaseUpload(
            io.BytesIO(data),
            mimetype=content_type or "application/octet-stream",
            resumable=False,
        )
        meta = {"name": filename, "parents": [settings.GDRIVE_RECEIPTS_FOLDER_ID]}
        f = (
            drive.files()
            .create(body=meta, media_body=media, fields="id, webViewLink")
            .execute()
        )
        return f.get("id"), f.get("webViewLink")
    except Exception:
        logger.exception("Drive receipt upload failed for %s", filename)
        return None, None
