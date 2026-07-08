"""Business Settings — the Owner Portal's financial control centre.

Maintain recurring fixed expenses and the three tunable assumptions (desired
profit, contribution margin, growth target). No code change is ever needed to
add or change an expense; setting changes are appended as history, never
overwritten.
"""
from datetime import date
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.web.deps import _tmpl, require_user
from app.services import business_settings as bs
from app.models.business import (
    SETTING_DESIRED_PROFIT,
    SETTING_CONTRIBUTION_MARGIN_PCT,
    SETTING_GROWTH_PCT,
    FREQUENCY_MONTHS,
)

router = APIRouter(tags=["business-settings"])


def _page_ctx(db: Session, user, **extra) -> dict:
    rows, total = bs.category_summary(db)
    fin = bs.get_financials(db)
    ctx = {
        "user": user,
        "summary_rows": [r for r in rows if r["monthly"] > 0],
        "fixed_total": total,
        "financials": fin,
        "expenses": bs.get_all_expenses(db),
        "categories": bs.CATEGORIES,
        "frequency_labels": bs.FREQUENCY_LABELS,
        "today": date.today().isoformat(),
    }
    ctx.update(extra)
    return ctx


def _parse_date(s: str | None):
    s = (s or "").strip()
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


@router.get("/business-settings", response_class=HTMLResponse)
def business_settings(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    return _tmpl(request, "business_settings.html", _page_ctx(db, user, saved=request.query_params.get("saved")))


@router.post("/business-settings/expenses")
async def add_expense(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    frequency: str = Form(...),
    effective_from: str = Form(default=""),
    effective_to: str = Form(default=""),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    try:
        if frequency not in FREQUENCY_MONTHS:
            raise ValueError("Invalid frequency")
        bs.create_expense(
            db, name=name, category=category, amount=float(amount), frequency=frequency,
            effective_from=_parse_date(effective_from) or date.today(),
            effective_to=_parse_date(effective_to), notes=notes,
        )
    except Exception as exc:
        db.rollback()
        return _tmpl(request, "business_settings.html",
                     _page_ctx(db, user, error=f"Could not add expense: {exc}"), status_code=400)
    return RedirectResponse("/business-settings?saved=1", status_code=303)


@router.get("/business-settings/expenses/{expense_id}/edit", response_class=HTMLResponse)
def edit_expense_get(expense_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    exp = bs.get_expense(db, expense_id)
    if not exp:
        return RedirectResponse("/business-settings", status_code=303)
    return _tmpl(request, "business_expense_edit.html", {
        "user": user, "exp": exp,
        "categories": bs.CATEGORIES, "frequency_labels": bs.FREQUENCY_LABELS, "error": None,
    })


@router.post("/business-settings/expenses/{expense_id}")
async def edit_expense_post(
    expense_id: int,
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    frequency: str = Form(...),
    effective_from: str = Form(default=""),
    effective_to: str = Form(default=""),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    exp = bs.get_expense(db, expense_id)
    if not exp:
        return RedirectResponse("/business-settings", status_code=303)
    try:
        if frequency not in FREQUENCY_MONTHS:
            raise ValueError("Invalid frequency")
        bs.update_expense(
            db, expense_id, name=name, category=category, amount=float(amount), frequency=frequency,
            effective_from=_parse_date(effective_from) or exp.effective_from,
            effective_to=_parse_date(effective_to), notes=(notes.strip() or None),
        )
    except Exception as exc:
        db.rollback()
        return _tmpl(request, "business_expense_edit.html",
                     {"user": user, "exp": exp, "categories": bs.CATEGORIES,
                      "frequency_labels": bs.FREQUENCY_LABELS, "error": f"Could not save: {exc}"}, status_code=400)
    return RedirectResponse("/business-settings?saved=1", status_code=303)


@router.post("/business-settings/expenses/{expense_id}/toggle")
async def toggle_expense(expense_id: int, request: Request, active: str = Form(...), db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    bs.set_expense_active(db, expense_id, active == "1")
    return RedirectResponse("/business-settings?saved=1", status_code=303)


@router.post("/business-settings/financials")
async def update_financials(
    request: Request,
    desired_profit: str = Form(...),
    margin_pct: str = Form(...),
    growth_pct: str = Form(...),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    try:
        current = bs.get_financials(db)
        pairs = [
            (SETTING_DESIRED_PROFIT, float(desired_profit), current["desired_profit"]),
            (SETTING_CONTRIBUTION_MARGIN_PCT, float(margin_pct), current["margin_pct"]),
            (SETTING_GROWTH_PCT, float(growth_pct), current["growth_pct"]),
        ]
        # Append a new history row only for values that actually changed.
        for key, new_val, cur_val in pairs:
            if abs(float(new_val) - float(cur_val)) > 1e-9:
                bs.set_setting(db, key, new_val, created_by=getattr(user, "username", None))
    except Exception as exc:
        db.rollback()
        return _tmpl(request, "business_settings.html",
                     _page_ctx(db, user, error=f"Could not save: {exc}"), status_code=400)
    return RedirectResponse("/business-settings?saved=1", status_code=303)
