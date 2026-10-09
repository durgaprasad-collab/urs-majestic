"""The Buy inbox: one list of everything that needs buying.

Two sources, merged per ingredient:
  * staff requests from the app -- item name only; the owner sets quantity
  * the stock itself -- items at zero or with <= LOW_COVER_DAYS of cover
    (v_ingredient_reorder_forecast, which reads the real stock ledger)

Approving an item records an approved Requisition carrying the owner's
quantity. It closes by itself when a matching purchase is logged
(api.routes.requisitions._sync_fulfillment), so the "waiting" list is just
approved requisitions.
"""
import datetime
import math
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.core.clock import business_now, business_tz
from app.models.requisition import Requisition, RequisitionStatus
from app.services.stock_count import NON_STOCK_CATEGORIES, NON_STOCK_NAMES

LOW_COVER_DAYS = 3
# No logged purchase for this long while the stock says it's out usually
# means it's being bought without being logged.
UNLOGGED_DAYS = 25
# Marks requisitions the owner created from the stock list (not staff asks),
# so Undo can remove them instead of sending them back to "pending".
OWNER_NOTE = "Buy list"

WHERE = {"Vegetables": "Market · vegetables", "Dairy": "Dairy", "Frozen": "Frozen",
         "Utilities": "Packaging", "packaging": "Packaging"}
GROUP_ORDER = ["Staff requests", "Market · vegetables", "Dairy", "Grocery & Hyperpure", "Frozen", "Packaging"]


def where_bought(category: str | None) -> str:
    return WHERE.get(category or "", "Grocery & Hyperpure")


def _suggested(row) -> Decimal | None:
    q = row["suggested_order_qty"]
    if q is None or q <= 0:
        q = row["last_qty"]
    if q is None or q <= 0:
        return None
    q = Decimal(str(q))
    if row["unit"] in ("pcs", "g", "ml"):
        return Decimal(math.ceil(q))
    return q.quantize(Decimal("0.01"))


def _forecast(db: Session) -> dict[int, dict]:
    rows = db.execute(text("""
        SELECT ingredient_id, name, category, unit, on_hand_qty, stock_days_cover_left, status,
               suggested_order_qty, last_qty, last_purchase, avg_unit_cost, daily_consumption
          FROM v_ingredient_reorder_forecast WHERE is_active
    """)).mappings().all()
    return {r["ingredient_id"]: dict(r) for r in rows
            if (r["category"] or "") not in NON_STOCK_CATEGORIES and r["name"] not in NON_STOCK_NAMES}


def _row(f: dict | None, today: datetime.date) -> dict:
    if f is None:
        return {"ingredient_id": None, "unit": None, "on_hand": None, "cover": None, "per_day": None,
                "qty": None, "unit_cost": None, "last": None, "days_since": None, "category": None}
    last = f["last_purchase"]
    return {
        "ingredient_id": f["ingredient_id"], "unit": f["unit"], "category": f["category"],
        "on_hand": f["on_hand_qty"], "cover": f["stock_days_cover_left"], "per_day": f["daily_consumption"],
        "qty": _suggested(f), "unit_cost": f["avg_unit_cost"],
        "last": last, "days_since": (today - last).days if last else None,
    }


def inbox(db: Session) -> dict:
    today = business_now().date()
    fc = _forecast(db)
    reqs = (db.query(Requisition)
            .options(joinedload(Requisition.requested_by), joinedload(Requisition.decided_by))
            .filter(Requisition.status.in_([RequisitionStatus.pending, RequisitionStatus.approved]))
            .order_by(Requisition.created_at).all())
    waiting = [r for r in reqs if r.status == RequisitionStatus.approved]
    waiting_ids = {r.ingredient_id for r in waiting if r.ingredient_id}

    items: dict[str, dict] = {}
    for r in reqs:
        if r.status != RequisitionStatus.pending:
            continue
        key = f"i{r.ingredient_id}" if r.ingredient_id else f"r{r.id}"
        it = items.get(key)
        if it is None:
            it = items[key] = {"key": key, "name": r.item_name, "requests": [],
                               **_row(fc.get(r.ingredient_id) if r.ingredient_id else None, today)}
        it["requests"].append({"id": r.id, "by": r.requested_by.name,
                               "at": r.created_at.astimezone(business_tz())})

    for iid, f in fc.items():
        key = f"i{iid}"
        if key in items or iid in waiting_ids:
            continue
        out = f["on_hand_qty"] is not None and f["on_hand_qty"] <= 0 and (f["daily_consumption"] or 0) > 0
        low = f["on_hand_qty"] is not None and f["stock_days_cover_left"] is not None and f["stock_days_cover_left"] <= LOW_COVER_DAYS
        # Never stock-tracked: fall back to the buying rhythm.
        cadence = f["on_hand_qty"] is None and f["status"] in ("due", "overdue")
        if out or low or cadence:
            items[key] = {"key": key, "name": f["name"], "requests": [], **_row(f, today)}

    for it in items.values():
        on_hand, cover = it["on_hand"], it["cover"]
        it["out"] = on_hand is not None and on_hand <= 0
        it["low"] = not it["out"] and cover is not None and cover <= LOW_COVER_DAYS
        it["cadence"] = on_hand is None and it["ingredient_id"] is not None
        it["unlogged"] = (not it["requests"] and it["out"] and it["days_since"] is not None
                          and it["days_since"] > UNLOGGED_DAYS)
        it["cost"] = (it["unit_cost"] * it["qty"]) if it["unit_cost"] and it["qty"] else None
        it["group"] = "Staff requests" if it["ingredient_id"] is None else where_bought(it["category"])

    groups = []
    for g in GROUP_ORDER:
        rows = [i for i in items.values() if i["group"] == g]
        if rows:
            rows.sort(key=lambda i: (not i["requests"], not i["out"], i["cover"] if i["cover"] is not None else 99, i["name"].lower()))
            groups.append((g, rows))

    all_items = list(items.values())
    return {
        "groups": groups,
        "n_items": len(all_items),
        "n_out": sum(1 for i in all_items if i["out"]),
        "n_low": sum(1 for i in all_items if i["low"]),
        "n_requests": sum(len(i["requests"]) for i in all_items),
        "total": sum((i["cost"] for i in all_items if i["cost"]), Decimal(0)),
        "waiting": waiting,
    }


def bought_this_week(db: Session) -> list[dict]:
    since = business_now().date() - datetime.timedelta(days=6)
    return [dict(r) for r in db.execute(text("""
        SELECT p.id, i.name, p.qty, p.unit::text AS unit, p.total_price, p.purchase_date, u.name AS by_name,
               EXISTS (SELECT 1 FROM requisitions r WHERE r.matched_purchase_id = p.id) AS from_list
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
          LEFT JOIN users u ON u.id = p.entered_by_user_id
         WHERE p.deleted_at IS NULL AND p.purchase_date >= :since
         ORDER BY p.purchase_date DESC, p.id DESC
    """), {"since": since}).mappings().all()]
