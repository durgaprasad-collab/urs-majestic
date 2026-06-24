"""Public feedback endpoint — writes ONLY to customer_feedback table."""
import re
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.feedback_ratelimit import feedback_is_rate_limited
from app.core.ratelimit import _get_ip
from app.models.customer_feedback import CustomerFeedback

router = APIRouter(tags=["public"])

_FAKE_PHONE_RE = re.compile(r'^(\d)\1{9}$')  # all same digit: 9999999999 etc


def _normalize_phone(raw: str) -> str | None:
    p = re.sub(r'[\s\-\.\(\)]', '', raw.strip())
    if p.startswith('+91'):
        p = p[3:]
    elif p.startswith('91') and len(p) == 12:
        p = p[2:]
    if not re.fullmatch(r'[6-9]\d{9}', p):
        return None
    if _FAKE_PHONE_RE.match(p):
        return None
    return p


class FeedbackIn(BaseModel):
    name: str
    phone: str
    rating: int | None = None
    review: str | None = None
    consent: bool


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


@router.post("/api/feedback")
async def post_feedback(body: FeedbackIn, request: Request, db: Session = Depends(get_db)):
    ip = _get_ip(request)
    if feedback_is_rate_limited(ip):
        return JSONResponse(
            {"ok": False, "error": "Too many submissions. Please wait a minute."},
            status_code=429,
        )

    if not body.consent:
        return _err("Consent is required to store your feedback.")

    name = body.name.strip()
    if not name:
        return _err("Name is required.")
    if len(name) > 200:
        return _err("Name is too long.")

    phone = _normalize_phone(body.phone)
    if phone is None:
        return _err("Please enter a valid 10-digit Indian mobile number.")

    if body.rating is not None and not (1 <= body.rating <= 5):
        return _err("Rating must be between 1 and 5.")

    review = body.review.strip() if body.review else None
    if review and len(review) > 500:
        return _err("Review must be under 500 characters.")

    db.add(CustomerFeedback(
        name=name,
        phone=phone,
        rating=body.rating,
        review=review,
        consent=True,
        source="qr_counter",
    ))
    db.commit()
    return JSONResponse({"ok": True})
