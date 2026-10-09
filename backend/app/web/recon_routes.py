"""Data-reconciliation dashboard: cross-checks each channel's declared sales
against what was actually ingested (v_recon_daily / v_recon_channel_status /
v_data_trust). Distinct from the existing /reconciliation (purchase cost)
page — this one is about upload data trust."""
import decimal
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.web.deps import _tmpl, require_user
from app.services.recon import get_data_trust, get_channel_status, get_daily_recon, upsert_exception

router = APIRouter(tags=["web"])


@router.get("/data-reconciliation")
def data_reconciliation():
    """Folded into Reconcile's channel data check (2026-10-09)."""
    return RedirectResponse("/reconciliation#channels", status_code=301)


@router.post("/data-reconciliation/explain")
def data_reconciliation_explain(
    request: Request,
    business_date: str = Form(...),
    channel: str = Form(...),
    explanation: str = Form(...),
    acknowledged_by: str = Form(...),
    expected: str = Form(None),
    actual: str = Form(None),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir

    from datetime import date as _date
    upsert_exception(
        db,
        business_date=_date.fromisoformat(business_date),
        channel=channel,
        explanation=explanation.strip(),
        acknowledged_by=acknowledged_by.strip(),
        expected=decimal.Decimal(expected) if expected else None,
        actual=decimal.Decimal(actual) if actual else None,
    )
    db.commit()

    return RedirectResponse("/reconciliation?notice=Explanation+saved.#channels", status_code=303)
