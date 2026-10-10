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
from app.services import request_check
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
    # Requests typed with a name that isn't an item ("Basmati rice"): match
    # them now so they get stock and a validity check. The owner can change it.
    relinked = False
    for r in reqs:
        if r.status == RequisitionStatus.pending and r.ingredient_id is None:
            iid = request_check.match_ingredient(db, r.item_name)
            if iid:
                r.ingredient_id, relinked = iid, True
    if relinked:
        db.commit()
    waiting = [r for r in reqs if r.status == RequisitionStatus.approved]
    waiting_ids = {r.ingredient_id for r in waiting if r.ingredient_id}

    items: dict[str, dict] = {}
    for r in reqs:
        if r.status != RequisitionStatus.pending:
            continue
        key = f"i{r.ingredient_id}" if r.ingredient_id else f"r{r.id}"
        it = items.get(key)
        if it is None:
            f = fc.get(r.ingredient_id) if r.ingredient_id else None
            it = items[key] = {"key": key, "name": f["name"] if f else r.item_name, "requests": [],
                               **_row(fc.get(r.ingredient_id) if r.ingredient_id else None, today)}
        it["requests"].append({"id": r.id, "by": r.requested_by.name, "asked": r.item_name,
                               "at": r.created_at.astimezone(business_tz()), "created": r.created_at})

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
        it["check"] = None
        if it["requests"] and it["ingredient_id"]:
            # Judged as of the first request, so later buys don't hide why it was asked.
            it["check"] = request_check.check(db, it["ingredient_id"], before=min(r["created"] for r in it["requests"]))
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


# ── Next 7 days ──────────────────────────────────────────────────────────────
WEEK_DAYS = 7
# A week's top-up smaller than this share of the usual purchase isn't worth
# a line (26 ml of cream); the item is effectively covered.
MIN_LINE_SHARE = Decimal("0.25")


def _round_up(q: Decimal, unit: str, step) -> Decimal:
    if step and Decimal(str(step)) > 0:
        step = Decimal(str(step))
        return (q / step).to_integral_value(rounding="ROUND_CEILING") * step
    if unit in ("pcs", "g", "ml"):
        return Decimal(math.ceil(q))
    return (q * 10).to_integral_value(rounding="ROUND_CEILING") / 10


def week_plan(db: Session, today_keys: set[str] | None = None) -> dict:
    """What runs out on which day over the next WEEK_DAYS, and one order that
    covers the week: daily use x 7 - stock, rounded to how it's bought.
    today_keys = inbox item keys, to mark lines already on today's list."""
    today = business_now().date()
    today_keys = today_keys or set()
    steps = {r[0]: r[1] for r in db.execute(text("SELECT id, order_increment_qty FROM ingredients"))}
    waiting = {r[0] for r in db.execute(text(
        "SELECT ingredient_id FROM requisitions WHERE status = 'approved' AND ingredient_id IS NOT NULL"))}
    lines, covered = [], 0
    for iid, f in _forecast(db).items():
        per_day, on_hand = f["daily_consumption"], f["on_hand_qty"]
        if not per_day or per_day <= 0 or on_hand is None:
            continue
        per_day, on_hand = Decimal(str(per_day)), max(Decimal(str(on_hand)), Decimal(0))
        cover = on_hand / per_day
        need = per_day * WEEK_DAYS - on_hand
        if need <= 0:
            covered += 1
            continue
        qty = _round_up(need, f["unit"], steps.get(iid))
        usual = Decimal(str(f["last_qty"])) if f["last_qty"] and f["last_qty"] > 0 else None
        if on_hand > 0 and usual and qty < usual * MIN_LINE_SHARE:
            covered += 1
            continue
        day = min(int(cover), WEEK_DAYS - 1)
        lines.append({
            "ingredient_id": iid, "name": f["name"], "category": f["category"], "unit": f["unit"],
            "group": where_bought(f["category"]), "on_hand": on_hand, "per_day": per_day,
            "cover": cover, "day": day, "runout": today + datetime.timedelta(days=day),
            "qty": qty, "unit_cost": f["avg_unit_cost"],
            "cost": (Decimal(str(f["avg_unit_cost"])) * qty) if f["avg_unit_cost"] else None,
            "on_today": f"i{iid}" in today_keys, "approved": iid in waiting,
        })
    lines.sort(key=lambda l: (l["cover"], l["name"].lower()))
    days = []
    for n in range(WEEK_DAYS):
        d = today + datetime.timedelta(days=n)
        days.append({"date": d, "n": n, "items": [l for l in lines if l["day"] == n and l["cover"] < WEEK_DAYS]})
    groups = [(g, [l for l in lines if l["group"] == g]) for g in GROUP_ORDER if any(l["group"] == g for l in lines)]
    open_lines = [l for l in lines if not l["approved"]]
    biggest = max((l for l in open_lines if l["cost"]), key=lambda l: l["cost"], default=None)
    return {
        "days": days, "groups": groups, "lines": lines, "covered": covered,
        "n_runout": sum(1 for l in lines if l["cover"] < WEEK_DAYS),
        "total": sum((l["cost"] for l in open_lines if l["cost"]), Decimal(0)),
        "biggest": biggest,
    }
