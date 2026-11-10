"""Gravy page: last batch, gravy left, when to make the next. Logic: services/gravy.py."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import gravy
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["gravy"])


def _dec(v: str):
    try:
        d = Decimal(v)
    except (InvalidOperation, TypeError):
        return None
    return d if d >= 0 else None


def _day(v: str):
    try:
        return date.fromisoformat(v)
    except (ValueError, TypeError):
        return None


@router.get("/gravy", response_class=HTMLResponse)
def gravy_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    return _tmpl(request, "gravy.html", {"user": user, **gravy.page_data(db)})


@router.post("/gravy/batch")
def add_batch(
    request: Request,
    batch_date: str = Form(...),
    gross_kg: str = Form(...),
    vessel_kg: str = Form("4"),
    started_use_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """A new batch: weighed gross minus the vessel is the yield. The batch before it
    is marked finished the day before this one is first served."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    d, g, v = _day(batch_date), _dec(gross_kg), _dec(vessel_kg)
    if d is None or g is None or v is None or g <= v:
        return RedirectResponse("/gravy?error=batch", status_code=303)
    start = _day(started_use_date) or d + timedelta(days=1)
    db.execute(text("""
        UPDATE gravy_batches SET finished_date = :f WHERE finished_date IS NULL AND yield_kg IS NOT NULL AND batch_date < :d
    """), {"f": start - timedelta(days=1), "d": d})
    db.execute(text("""
        INSERT INTO gravy_batches (batch_date, gross_kg, vessel_kg, vessel_weighed, yield_kg, started_use_date, notes, created_by)
        VALUES (:d, :g, :v, false, :y, :s, :n, :u)
    """), {"d": d, "g": g, "v": v, "y": g - v, "s": start, "n": notes.strip() or None, "u": user.id})
    db.commit()
    return RedirectResponse("/gravy?added=batch", status_code=303)


@router.post("/gravy/reading")
def add_reading(
    request: Request,
    batch_id: int = Form(...),
    reading_date: str = Form(...),
    kg_left: str = Form(...),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    gravy.ensure_tables(db)
    d, k = _day(reading_date), _dec(kg_left)
    if d is None or k is None or k > 60:
        return RedirectResponse("/gravy?error=reading", status_code=303)
    db.execute(text("DELETE FROM gravy_readings WHERE batch_id = :b AND reading_date = :d"), {"b": batch_id, "d": d})
    db.execute(text("""
        INSERT INTO gravy_readings (batch_id, reading_date, kg_left, created_by) VALUES (:b, :d, :k, :u)
    """), {"b": batch_id, "d": d, "k": k, "u": user.id})
    db.commit()
    return RedirectResponse("/gravy?added=reading", status_code=303)


@router.post("/gravy/finish")
def finish_batch(
    request: Request,
    batch_id: int = Form(...),
    finished_date: str = Form(...),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    d = _day(finished_date)
    if d is None:
        return RedirectResponse("/gravy?error=finish", status_code=303)
    db.execute(text("UPDATE gravy_batches SET finished_date = :d WHERE id = :b"), {"d": d, "b": batch_id})
    db.commit()
    return RedirectResponse("/gravy?added=finish", status_code=303)
