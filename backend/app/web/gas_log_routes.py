"""Gas page: the two cylinders (Tandoor, Kitchen) weighed nightly at 12:30 AM
by staff in the app -- gas left, use per night, cost per dish, bills check.
Owners can add a reading and correct one in place. Logic: services/gas.py.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_tz
from app.core.database import get_db
from app.services import gas
from app.web.audit import log_change
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["gas-log"])

_MIN = float(gas.TARE_KG) - 0.5
_MAX = float(gas.TARE_KG + gas.FULL_KG) + 3


@router.get("/gas-log", response_class=HTMLResponse)
def gas_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    return _tmpl(request, "gas_log.html", {"user": user, **gas.page_data(db), "TARE": gas.TARE_KG})


@router.post("/gas-log/reading")
def add_reading(
    request: Request,
    cylinder: str = Form(...),
    gross_kg: float = Form(...),
    is_new_cylinder: str = Form(None),
    recorded_at: str = Form(""),
    db: Session = Depends(get_db),
):
    """Owner adds a reading (e.g. one sent on WhatsApp). recorded_at is a
    local datetime-local value; blank means now."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    if cylinder not in gas.LABEL or not (_MIN <= gross_kg <= _MAX):
        return RedirectResponse(url="/gas-log?error=1", status_code=303)
    at = datetime.now(timezone.utc)
    if recorded_at:
        try:
            at = datetime.fromisoformat(recorded_at).replace(tzinfo=business_tz())
        except ValueError:
            pass
    db.execute(text("""
        INSERT INTO gas_readings (cylinder, cylinder_role, gross_kg, tare_kg, is_new_cylinder, recorded_by, recorded_at, note)
        VALUES (:c, 'in_use', :g, :t, :n, :u, :at, 'added on the Gas page')
    """), {"c": cylinder, "g": gross_kg, "t": gas.TARE_KG, "n": bool(is_new_cylinder), "u": user.id, "at": at})
    db.commit()
    return RedirectResponse(url="/gas-log?added=1", status_code=303)


@router.post("/gas-log/reading/{reading_id}")
async def correct_reading(reading_id: int, request: Request, db: Session = Depends(get_db)):
    """One-step correction from the readings table (JSON: {gross_kg})."""
    user, redir = require_user(request, db)
    if redir:
        return JSONResponse({"error": "Sign in again"}, status_code=401)
    body = await request.json()
    try:
        g = Decimal(str(body.get("gross_kg")))
    except (InvalidOperation, TypeError):
        return JSONResponse({"error": "Not a number"}, status_code=400)
    if not (_MIN <= float(g) <= _MAX):
        return JSONResponse({"error": f"{g} kg doesn't look like a cylinder on the scale"}, status_code=400)
    old = db.execute(text("SELECT gross_kg FROM gas_readings WHERE id = :i"), {"i": reading_id}).scalar()
    if old is None:
        return JSONResponse({"error": "Reading not found"}, status_code=404)
    log_change(db, batch="gas_reading_edit", target_table="gas_readings", target_id=reading_id, field="gross_kg",
               old_value=old, new_value=g, reason="Corrected on the Gas page", actor_user_id=user.id)
    db.execute(text("UPDATE gas_readings SET gross_kg = :g WHERE id = :i"), {"g": g, "i": reading_id})
    db.commit()
    return JSONResponse({"ok": True})
