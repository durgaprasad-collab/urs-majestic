"""Buy: the owners' purchasing inbox (replaces the Requisitions page).

Staff requests (item name only) and items the stock says are running out,
in one list. Approving records an approved requisition with the owner's
quantity; it closes itself when the purchase is logged.
"""
import json
import logging
import re
from urllib.parse import urlencode
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.routes.requisitions import _sync_fulfillment, notify_decision
from app.core.clock import business_now, business_tz
from app.core.database import get_db
from app.models.ingredient import Ingredient
from app.models.requisition import Requisition, RequisitionStatus
from app.models.user import User
from app.services import request_check
from app.services import vendor_prices
from app.services.buy_inbox import OWNER_NOTE, WEEK_NOTE, bought_this_week, inbox, week_plan
from app.web.deps import _tmpl, require_user

log = logging.getLogger("buy")
router = APIRouter(tags=["buy"])

_UNITS = {"kg", "g", "l", "ml", "pcs"}


@router.get("/buy", response_class=HTMLResponse)
def buy_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    open_reqs = (db.query(Requisition)
                 .filter(Requisition.status.in_([RequisitionStatus.approved, RequisitionStatus.pending])).all())
    if _sync_fulfillment(db, open_reqs):
        db.commit()
    data = inbox(db)
    for r in data["waiting"]:
        r.decided_local = r.decided_at.astimezone(business_tz()) if r.decided_at else None
    keys = {i["key"] for _, rows in data["groups"] for i in rows}
    item_options = (db.query(Ingredient.id, Ingredient.name).filter(Ingredient.is_active.is_(True))
                    .order_by(Ingredient.name).all()) if data["n_requests"] else []
    staff_users = (db.query(User).filter(User.is_active.is_(True), User.username != "claude_bot")
                   .order_by(User.is_admin, User.name).all())
    return _tmpl(request, "buy.html", {"user": user, **data, "bought": bought_this_week(db), "week": week_plan(db, keys),
                                       "item_options": item_options, "tz": business_tz(), "staff_users": staff_users,
                                       "vendors": vendor_prices.compare(db)["items"], "market": vendor_prices.market_latest(db)})


@router.get("/buy/week/print", response_class=HTMLResponse)
def week_print(request: Request, db: Session = Depends(get_db)):
    """Supplier sheet for the week's order: grouped by where it's bought,
    printable / save-as-PDF from the browser. ?ids= limits it to the lines
    ticked on the page."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    plan = week_plan(db)
    ids = {int(x) for x in request.query_params.get("ids", "").split(",") if x.isdigit()}
    qty = {}
    for part in request.query_params.get("q", "").split(","):
        k, _, v = part.partition(":")
        if k.isdigit():
            try:
                qty[int(k)] = Decimal(v)
            except InvalidOperation:
                pass
    groups = []
    for g, lines in plan["groups"]:
        chosen = [dict(l, qty=qty.get(l["ingredient_id"], l["qty"])) for l in lines
                  if not l["approved"] and (not ids or l["ingredient_id"] in ids)]
        if chosen:
            groups.append((g, chosen))
    return _tmpl(request, "buy_week_print.html", {"user": user, "groups": groups, "printed": business_now()})


@router.get("/requisitions")
def old_requisitions_page():
    return RedirectResponse(url="/buy", status_code=301)


# Order Forecast and Weekly Ordering were folded into Buy's "Next 7 days" tab.
@router.get("/order-forecast")
@router.get("/weekly-order")
@router.get("/weekly-order/{rest:path}")
def old_forecast_pages(rest: str = ""):
    return RedirectResponse(url="/buy?tab=week", status_code=301)


def _qty(value) -> Decimal | None:
    try:
        q = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return q if q > 0 else None


@router.post("/buy/approve")
def approve(request: Request, items: str = Form("[]"), db: Session = Depends(get_db)):
    """items = JSON [{ingredient_id, requests: [ids], qty, unit}] from the page."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    try:
        rows = json.loads(items)
    except ValueError:
        rows = []
    now = datetime.now(timezone.utc)
    notify = []
    n = 0
    for row in rows if isinstance(rows, list) else []:
        qty = _qty(row.get("qty"))
        unit = row.get("unit") if row.get("unit") in _UNITS else None
        req_ids = [int(x) for x in row.get("requests") or [] if str(x).isdigit()]
        pending = (db.query(Requisition)
                   .filter(Requisition.id.in_(req_ids), Requisition.status == RequisitionStatus.pending).all()
                   if req_ids else [])
        if pending:
            for req in pending:
                req.status = RequisitionStatus.approved
                req.quantity, req.unit = qty, unit if qty else None
                req.decided_by_user_id, req.decided_at = user.id, now
                notify.append(req)
            n += 1
            continue
        iid = row.get("ingredient_id")
        ing = db.get(Ingredient, int(iid)) if str(iid or "").isdigit() else None
        if ing is None:
            continue
        db.add(Requisition(
            requested_by_user_id=user.id, item_name=ing.name, ingredient_id=ing.id,
            quantity=qty, unit=unit if qty else None, note=WEEK_NOTE if row.get("source") == "week" else OWNER_NOTE,
            status=RequisitionStatus.approved, decided_by_user_id=user.id, decided_at=now,
        ))
        n += 1
    db.commit()
    for req in notify:
        notify_decision(db, req, True)
    return RedirectResponse(url=f"/buy?approved={n}", status_code=303)


_QTY_RE = re.compile(r"(?:^|\s)(\d+(?:[.,]\d+)?\s*(?:kg|kgs|g|gm|gms|grams?|l|ltr|litres?|liters?|ml|pcs?|pieces?|pkts?|packets?|nos?|bunch(?:es)?|box(?:es)?|x)?|x\s*\d+)\s*$", re.I)
_LEAD_RE = re.compile(r"^\s*(?:[-*•●]+|\d+[.)])\s*")


def parse_list(raw: str) -> list[tuple[str, str]]:
    """A list pasted from WhatsApp / typed from a call: one item per line (commas
    and semicolons also split). Returns [(name, qty hint)], bullets and numbering
    removed, a trailing or leading quantity kept as a hint ("Onion 5 kg")."""
    out, seen = [], set()
    for part in re.split(r"[\r\n,;]+", raw or ""):
        t = _LEAD_RE.sub("", part).strip(" .:-\t")
        if not t:
            continue
        hint = ""
        m = _QTY_RE.search(t)
        if m and m.start() > 0:
            hint, t = m.group(1).strip(), t[:m.start()].strip(" -:")
        else:
            m2 = re.match(r"^(\d+(?:[.,]\d+)?\s*(?:kg|g|l|ml|pcs?|pkts?|nos?)?)\s+(.+)$", t, re.I)
            if m2:
                hint, t = m2.group(1).strip(), m2.group(2).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append((t[:120], hint))
    return out


@router.post("/buy/proxy")
async def proxy_requests(request: Request, db: Session = Depends(get_db)):
    """Owner enters a staff member's list (sent by WhatsApp, a call, a slip):
    each item becomes a pending request in that staff member's name."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    form = await request.form()
    staff = db.get(User, int(form.get("staff_id"))) if str(form.get("staff_id") or "").isdigit() else None
    if staff is None or not staff.is_active:
        return RedirectResponse(url="/buy?proxy_error=Pick+who+sent+the+list", status_code=303)
    items = parse_list(str(form.get("items") or ""))
    if not items:
        return RedirectResponse(url="/buy?proxy_error=Type+or+paste+at+least+one+item", status_code=303)
    added, skipped, unmatched = [], [], []
    for name, hint in items:
        name, shelf = request_check.split_shelf(name)
        iid = request_check.match_ingredient(db, name)
        ing = db.get(Ingredient, iid) if iid else None
        shown = ing.name if ing else name
        q = db.query(Requisition).filter(Requisition.status.in_([RequisitionStatus.pending, RequisitionStatus.approved]))
        q = q.filter((Requisition.ingredient_id == iid) | Requisition.item_name.ilike(name)) if iid else q.filter(Requisition.item_name.ilike(name))
        if q.first():
            skipped.append(shown)
            continue
        note = (f"Entered by {user.name} for {staff.name}" + (f" · asked {hint}" if hint else "")
                + (f" · {shelf} left on shelf" if shelf else ""))
        db.add(Requisition(requested_by_user_id=staff.id, item_name=name if not ing else ing.name,
                           ingredient_id=iid, note=note[:250]))
        added.append(shown)
        if not ing:
            unmatched.append(name)
    db.commit()
    log.info("proxy requests by %s for %s: added=%s skipped=%s", user.username, staff.username, added, skipped)
    msg = f"{len(added)} request{'s' if len(added) != 1 else ''} added for {staff.name}"
    if skipped:
        msg += f" · already requested: {', '.join(skipped)}"
    if unmatched:
        msg += f" · not in the item list (pick the item on the row): {', '.join(unmatched)}"
    return RedirectResponse(url=f"/buy?{urlencode({'proxy': msg})}", status_code=303)


@router.post("/buy/requests/{requisition_id}/reject")
def reject(requisition_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    req = db.get(Requisition, requisition_id)
    if req and req.status == RequisitionStatus.pending:
        req.status = RequisitionStatus.rejected
        req.decided_by_user_id = user.id
        req.decided_at = datetime.now(timezone.utc)
        db.commit()
        notify_decision(db, req, False)
    return RedirectResponse(url="/buy", status_code=303)


@router.post("/buy/requests/{requisition_id}/ask-check")
def ask_check(requisition_id: int, request: Request, db: Session = Depends(get_db)):
    """Send the request back with what the recipes say is left. Staff see the
    note in the app and can request it again if it has really run out."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    req = db.get(Requisition, requisition_id)
    if req and req.status == RequisitionStatus.pending:
        c = request_check.check(db, req.ingredient_id, before=req.created_at) if req.ingredient_id else None
        hint = ""
        if c and c.get("left_hint"):
            hint = f" Recipes say about {c['left_hint']} should be left."
        elif c and c["last"]:
            hint = f" Last bought {c['last']['d']:%d %b} ({c['fmt']['last_qty']})."
        req.status = RequisitionStatus.rejected
        req.decided_by_user_id = user.id
        req.decided_at = datetime.now(timezone.utc)
        req.decision_note = f"Please check the stock first.{hint} If it has run out, send it again."
        db.commit()
        notify_decision(db, req, False)
    return RedirectResponse(url="/buy", status_code=303)


@router.post("/buy/requests/{requisition_id}/match")
async def rematch(requisition_id: int, request: Request, db: Session = Depends(get_db)):
    """Owner picks which item a typed request name means. The typed name is
    remembered, so the same wording is matched by itself next time, and any
    other open request typed the same way is linked now too."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    form = await request.form()
    req = db.get(Requisition, requisition_id)
    raw = str(form.get("ingredient_id") or "")
    open_states = [RequisitionStatus.pending, RequisitionStatus.approved]
    if req and req.status in open_states and raw.isdigit() and db.get(Ingredient, int(raw)):
        iid = int(raw)
        typed = req.item_name
        req.ingredient_id = iid
        if form.get("remember", "1") == "1":
            request_check.learn_alias(db, typed, iid, user.id)
            same = (db.query(Requisition).filter(Requisition.status.in_(open_states), Requisition.ingredient_id.is_(None),
                                                 Requisition.item_name.ilike(typed), Requisition.id != req.id).all())
            for other in same:
                other.ingredient_id = iid
        db.commit()
        log.info("request %s '%s' mapped to item %s by %s", req.id, typed, iid, user.username)
    return RedirectResponse(url="/buy", status_code=303)


@router.post("/buy/approved/{requisition_id}/undo")
def undo(requisition_id: int, request: Request, db: Session = Depends(get_db)):
    """Take an approval back: an owner-added line is removed, a staff request
    goes back to waiting for a decision."""
    user, redir = require_user(request, db)
    if redir:
        return redir
    req = db.get(Requisition, requisition_id)
    if req and req.status == RequisitionStatus.approved:
        owner_added = req.note in (OWNER_NOTE, WEEK_NOTE) or (req.note or "").startswith("Catering #")
        if owner_added and req.requested_by_user_id == req.decided_by_user_id:
            db.delete(req)
        else:
            req.status = RequisitionStatus.pending
            req.decided_by_user_id = req.decided_at = None
            req.quantity = req.unit = None
        db.commit()
    return RedirectResponse(url="/buy?tab=wait", status_code=303)
