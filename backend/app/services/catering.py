"""Catering orders: board, money (food cost + margin from the menu recipes),
the ingredients an order needs, and sending the short ones to Buy.

Stages: quote -> confirmed -> in_prep -> delivered (or cancelled). Payment is
separate: advance_paid / balance_due / payment_status. A line is either a menu
dish (menu_item_id: cost and recipe known) or a custom item (no cost).
"""
import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload

from app.core.clock import business_now
from app.models.catering_order import CateringOrder, CateringOrderStatus, CateringPaymentStatus
from app.models.requisition import Requisition, RequisitionStatus
from app.services.sales_stock import Sale, calculate_ingredient_usage
from app.services.stock_count import NON_STOCK_CATEGORIES

STAGES = [("quote", "Quote"), ("confirmed", "Confirmed"), ("in_prep", "In prep"), ("delivered", "Delivered")]
NEXT_DAYS = 14
BUY_NOTE = "Catering"


def food_costs(db: Session) -> dict[int, Decimal]:
    return {r[0]: Decimal(str(r[1])) for r in db.execute(text(
        "SELECT id, food_cost FROM v_menu_item_full_cost WHERE food_cost IS NOT NULL"))}


def menu_options(db: Session) -> list[dict]:
    costs = food_costs(db)
    rows = db.execute(text("SELECT id, name, price, category FROM menu_items WHERE is_active AND is_food ORDER BY name")).mappings()
    return [{"id": r["id"], "name": r["name"], "price": float(r["price"] or 0), "category": r["category"],
             "cost": float(costs[r["id"]]) if r["id"] in costs else None} for r in rows]


def money(order: CateringOrder, costs: dict[int, Decimal]) -> dict:
    # The order total (after any order-level discount, e.g. Deepak's ₹415);
    # lines alone would overstate it.
    total = order.total_amount if order.total_amount is not None else sum((i.amount for i in order.items), Decimal(0))
    at_menu = sum(((i.menu_price or (i.amount / i.quantity if i.quantity else 0)) * i.quantity for i in order.items), Decimal(0))
    cost = sum((costs.get(i.menu_item_id, Decimal(0)) * i.quantity for i in order.items if i.menu_item_id), Decimal(0))
    uncosted = [i.item_name for i in order.items if not i.menu_item_id or i.menu_item_id not in costs]
    return {"total": total, "at_menu": at_menu, "discount": max(at_menu - total, Decimal(0)), "cost": cost,
            "margin": total - cost, "margin_pct": float((total - cost) / total * 100) if total else None,
            "uncosted": uncosted, "per_plate": (total / order.plates) if order.plates else None}


def board(db: Session) -> dict:
    orders = (db.query(CateringOrder).options(selectinload(CateringOrder.items))
              .order_by(CateringOrder.delivery_date, CateringOrder.delivery_time).all())
    costs = food_costs(db)
    today = business_now().date()
    cols = {k: [] for k, _ in STAGES}
    cancelled = []
    for o in orders:
        o.m = money(o, costs)
        (cancelled if o.status == CateringOrderStatus.cancelled else cols[o.status.value]).append(o)
    cols["delivered"].sort(key=lambda o: o.delivery_date, reverse=True)
    live = [o for o in orders if o.status != CateringOrderStatus.cancelled]
    upcoming = [o for o in live if o.status != CateringOrderStatus.delivered
                and today <= o.delivery_date <= today + datetime.timedelta(days=NEXT_DAYS)]
    delivered = [o for o in live if o.status == CateringOrderStatus.delivered]
    rev = sum((o.m["total"] for o in delivered), Decimal(0))
    cost = sum((o.m["cost"] for o in delivered), Decimal(0))
    return {
        "columns": [(k, label, cols[k]) for k, label in STAGES], "cancelled": cancelled,
        "upcoming": upcoming,
        "balance": sum((o.balance_due for o in live if o.status != CateringOrderStatus.quote), Decimal(0)),
        "balance_n": sum(1 for o in live if o.status != CateringOrderStatus.quote and o.balance_due > 0),
        "revenue": rev, "n_delivered": len(delivered),
        "margin_pct": float((rev - cost) / rev * 100) if rev else None,
        "uncosted_any": any(o.m["uncosted"] for o in delivered),
        "first": min((o.delivery_date for o in delivered), default=None),
        "last": max((o.delivery_date for o in delivered), default=None),
    }


def ingredients(db: Session, order: CateringOrder) -> list[dict]:
    """What the order's menu dishes need (recipes), vs stock now."""
    ids = [i.menu_item_id for i in order.items if i.menu_item_id]
    if not ids:
        return []
    names = dict(db.execute(text("SELECT id, name FROM menu_items WHERE id = ANY(:ids)"), {"ids": ids}).all())
    sales = [Sale(names[i.menu_item_id], Decimal(i.quantity), order.delivery_date) for i in order.items if i.menu_item_id]
    if not sales:
        return []
    usage, _ = calculate_ingredient_usage(db, sales)
    info = {r["id"]: r for r in db.execute(text("""
        SELECT i.id, i.name, i.category, v.on_hand_qty FROM ingredients i
        LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
    """)).mappings()}
    out = []
    for (_, iid), (qty, unit) in usage.items():
        r = info.get(iid)
        if not r or (r["category"] or "") in NON_STOCK_CATEGORIES or r["category"] == "Spices":
            continue
        oh = Decimal(str(r["on_hand_qty"])) if r["on_hand_qty"] is not None else None
        out.append({"ingredient_id": iid, "name": r["name"], "qty": qty, "unit": unit, "on_hand": oh,
                    "short": oh is not None and qty > oh})
    out.sort(key=lambda x: (not x["short"], x["name"].lower()))
    return out


def add_to_buy(db: Session, order: CateringOrder, user_id: int) -> tuple[int, int]:
    """Approved requisitions for the order's short ingredients (full amount
    needed -- catering is on top of normal service). Skips items that already
    have an open approval. Returns (added, skipped). Caller commits."""
    open_ids = {r[0] for r in db.execute(text(
        "SELECT ingredient_id FROM requisitions WHERE status = 'approved' AND ingredient_id IS NOT NULL"))}
    added = skipped = 0
    now = datetime.datetime.now(datetime.timezone.utc)
    for line in ingredients(db, order):
        if not line["short"]:
            continue
        if line["ingredient_id"] in open_ids:
            skipped += 1
            continue
        db.add(Requisition(
            requested_by_user_id=user_id, item_name=line["name"], ingredient_id=line["ingredient_id"],
            quantity=line["qty"], unit=line["unit"], status=RequisitionStatus.approved,
            decided_by_user_id=user_id, decided_at=now,
            note=f"{BUY_NOTE} #{order.id} {order.customer_name} · {order.delivery_date:%d %b}",
        ))
        added += 1
    order.buy_added_at = now
    return added, skipped


def payment_status(total: Decimal, advance: Decimal) -> CateringPaymentStatus:
    if total > 0 and advance >= total:
        return CateringPaymentStatus.paid
    return CateringPaymentStatus.partial if advance > 0 else CateringPaymentStatus.pending
