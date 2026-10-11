"""Rush slips (design/mockups/17-rush-slips.html).

The captain enters each order on the staff app. Every order is due
PROMISE_MIN after it's taken; each dish's start-by time is worked back from
that (due - pack - cook minutes) so all parts of an order finish together.
Station slips pool the same dish across open orders into batches; combos are
split into their component dishes (combo_components).
"""
import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_tz

PROMISE_MIN = 12
PACK_MIN = 1
# Same dish from different KOTs is cooked together when their start times are
# within this many minutes (owner, 11 Oct 2026: 3 minutes for every station).
POOL_WINDOW_MIN = {"T": 3, "W": 3, "F": 3, "G": 3, "C": 3}
BATCH = {"T": 8, "W": 3, "F": 4, "G": 99, "C": 4}
STATIONS = {"T": "Tandoor", "W": "Wok / Chinese", "G": "Gravy + rice", "F": "Fryer / tawa", "C": "Combos"}
ORDER_TYPES = ("Dine-in", "Parcel", "Zomato", "Swiggy")
OPEN_HOURS = 4  # an open order older than this drops off the board


def menu(db: Session) -> list[dict]:
    """Dishes the captain can tap, most-sold first, with how each one splits."""
    rows = db.execute(text("""
        SELECT mi.id, mi.name, mi.category, mi.kitchen_station AS st, mi.cook_minutes AS cook,
               coalesce((SELECT sum(qty) FROM item_sales s WHERE s.item_name = mi.name AND s.sale_date > current_date - 30), 0) AS sold
          FROM menu_items mi
         WHERE mi.is_active AND mi.is_food
         ORDER BY sold DESC, mi.name
    """)).mappings().all()
    parts = _combo_parts(db)
    return [{"id": r["id"], "name": r["name"], "category": r["category"], "station": r["st"],
             "cook": r["cook"], "sold": float(r["sold"]), "parts": parts.get(r["id"])} for r in rows]


def _combo_parts(db: Session) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in db.execute(text("""
        SELECT c.combo_menu_item_id AS cid, m.id, m.name, m.kitchen_station AS st, m.cook_minutes AS cook, c.portion_factor AS pf
          FROM combo_components c JOIN menu_items m ON m.id = c.component_menu_item_id
         ORDER BY c.id
    """)).mappings():
        out.setdefault(r["cid"], []).append({"id": r["id"], "name": r["name"], "station": r["st"],
                                             "cook": r["cook"], "factor": float(r["pf"])})
    return out


def create_order(db: Session, user_id: int, order_type: str, ref: str | None, items: list[dict],
                 kot_no: str | None = None) -> int:
    if order_type not in ORDER_TYPES:
        raise ValueError("Pick dine-in, parcel, Zomato or Swiggy")
    kot_no = (kot_no or "").strip()[:20]
    if not kot_no:
        raise ValueError("Enter the Petpooja KOT number")
    clean = [(int(i["menu_item_id"]), int(i["qty"])) for i in items if int(i.get("qty") or 0) > 0]
    if not clean:
        raise ValueError("Add at least one dish")
    oid = db.execute(text("""INSERT INTO kitchen_orders (order_type, ref, created_by, kot_no) VALUES (:t, :r, :u, :k) RETURNING id"""),
                     {"t": order_type, "r": (ref or "").strip()[:40] or None, "u": user_id, "k": kot_no}).scalar()
    for mid, qty in clean:
        db.execute(text("INSERT INTO kitchen_order_items (order_id, menu_item_id, qty) VALUES (:o, :m, :q)"),
                   {"o": oid, "m": mid, "q": qty})
    db.commit()
    return oid


def set_status(db: Session, order_id: int, status: str) -> None:
    if status not in ("packed", "cancelled"):
        raise ValueError("Unknown status")
    db.execute(text("""UPDATE kitchen_orders SET status = :s, packed_at = CASE WHEN :s = 'packed' THEN now() END
                       WHERE id = :i AND status = 'open'"""), {"s": status, "i": order_id})
    db.commit()


def set_item_ready(db: Session, item_id: int, ready: bool) -> None:
    """Food ready for one line of an open order (or undo)."""
    db.execute(text("""UPDATE kitchen_order_items ki SET ready_at = CASE WHEN :r THEN now() END
                         FROM kitchen_orders o
                        WHERE ki.id = :i AND o.id = ki.order_id AND o.status = 'open'"""), {"r": ready, "i": item_id})
    db.commit()


def _clock(dt: datetime.datetime) -> str:
    return dt.astimezone(business_tz()).strftime("%I:%M").lstrip("0")


def board(db: Session, only_order: int | None = None) -> dict:
    """Open orders, the pack list and one slip per station.

    only_order limits the station slips to that order's dishes (the slip
    printed when it's sent), still showing which other open orders share
    each dish so the cook can pool them."""
    now = datetime.datetime.now(datetime.timezone.utc)
    orders = db.execute(text("""
        SELECT o.id, o.order_type, o.ref, o.kot_no, o.created_at, u.name AS by
          FROM kitchen_orders o LEFT JOIN users u ON u.id = o.created_by
         WHERE o.status = 'open' AND o.created_at > now() - make_interval(hours => :h)
         ORDER BY o.created_at
    """), {"h": OPEN_HOURS}).mappings().all()
    ids = [o["id"] for o in orders]
    lines = db.execute(text("""
        SELECT ki.id AS line_id, ki.order_id, ki.qty, ki.ready_at, mi.id, mi.name, mi.kitchen_station AS st, mi.cook_minutes AS cook
          FROM kitchen_order_items ki JOIN menu_items mi ON mi.id = ki.menu_item_id
         WHERE ki.order_id = ANY(:ids) ORDER BY ki.id
    """), {"ids": ids or [0]}).mappings().all()
    combos = _combo_parts(db)

    out_orders, parts = [], []
    by_id = {o["id"]: o for o in orders}
    for o in orders:
        due = o["created_at"] + datetime.timedelta(minutes=PROMISE_MIN)
        mine = [l for l in lines if l["order_id"] == o["id"]]
        out_orders.append({"id": o["id"], "kot": o["kot_no"], "type": o["order_type"], "ref": o["ref"], "by": o["by"],
                           "taken": _clock(o["created_at"]), "due": _clock(due),
                           "mins_left": round((due - now).total_seconds() / 60),
                           "ready": sum(1 for l in mine if l["ready_at"]), "total": len(mine),
                           "items": [{"id": l["line_id"], "name": l["name"], "qty": l["qty"], "ready": bool(l["ready_at"])}
                                     for l in mine]})
    for l in lines:
        if l["ready_at"]:
            continue  # food ready: off the station slips
        o = by_id[l["order_id"]]
        due = o["created_at"] + datetime.timedelta(minutes=PROMISE_MIN)
        # Combos stay whole on the board (owner, 11 Oct: list the item, don't
        # split it): one line on the Combos card, timed by its slowest part.
        if l["id"] in combos:
            parts_ = combos[l["id"]]
            pieces = [{"name": l["name"], "station": "C", "factor": 1.0,
                       "cook": max((p["cook"] or 3) for p in parts_)}]
        else:
            pieces = [{"name": l["name"], "station": l["st"], "cook": l["cook"], "factor": 1.0}]
        for p in pieces:
            if not p["station"]:
                continue
            start = due - datetime.timedelta(minutes=PACK_MIN + (p["cook"] or 3))
            parts.append({"order": o["id"], "dish": p["name"], "st": p["station"], "qty": p["factor"] * l["qty"],
                          "start": start})

    slips = []
    for st, label in STATIONS.items():
        mine = sorted((p for p in parts if p["st"] == st), key=lambda p: p["start"])
        batches: list[dict] = []
        for p in mine:
            b = next((b for b in batches if b["dish"] == p["dish"]
                      and (p["start"] - b["start"]).total_seconds() <= POOL_WINDOW_MIN[st] * 60
                      and b["qty"] + p["qty"] <= BATCH[st] + 0.01), None)
            if b:
                b["qty"] += p["qty"]
                if p["order"] not in b["orders"]:
                    b["orders"].append(p["order"])
            else:
                batches.append({"dish": p["dish"], "qty": p["qty"], "start": p["start"], "orders": [p["order"]]})
        kot = {o["id"]: (o["kot_no"] or str(o["id"])) for o in orders}
        src = {o["id"]: o["order_type"] for o in orders}
        if only_order is not None:
            batches = [b for b in batches if only_order in b["orders"]]
        if not batches:
            continue
        slips.append({"station": st, "label": label, "batches": [{
            "dish": _dish(b["dish"]), "qty": _qty(b["qty"], b["dish"]), "orders": b["orders"],
            "kots": [kot[i] for i in b["orders"]],
            "tags": [f"{kot[i]} {src[i]}" for i in b["orders"]], "now": b["start"] <= now,
            "start": _clock(b["start"])} for b in sorted(batches, key=lambda b: b["start"])]})
    return {"printed": _clock(now), "promise_min": PROMISE_MIN, "orders": out_orders, "slips": slips,
            "pack": out_orders if only_order is None else
                    [o for o in out_orders if o["id"] == only_order]}


def _dish(name: str) -> str:
    return name.replace(" (2 Nos)", "").replace(" (1 No)", "")


def _qty(q: float, dish: str = "") -> str:
    # "Roti (2 Nos)" x 0.5 from a combo is one roti: count pieces for 2-piece breads.
    if "(2 Nos)" in dish:
        q = q * 2
        return f"{q:g} pc"
    if abs(q - round(q)) < 0.01:
        return f"×{int(round(q))}"
    if abs(q * 2 - round(q * 2)) < 0.01:
        return f"{int(round(q * 2))} half"
    return f"×{q:g}"


def history(db: Session, days: int = 1) -> dict:
    """Closed orders (packed or cancelled) from the last `days` business days,
    newest first, with time taken against the promise."""
    days = max(1, min(int(days or 1), 30))
    rows = db.execute(text("""
        SELECT o.id, o.kot_no, o.order_type, o.ref, o.status, o.created_at, o.packed_at, u.name AS by
          FROM kitchen_orders o LEFT JOIN users u ON u.id = o.created_by
         WHERE o.status IN ('packed', 'cancelled')
           AND o.created_at > (date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata' - interval '5 hours')
                               - make_interval(days => :d - 1) + interval '5 hours') AT TIME ZONE 'Asia/Kolkata'
         ORDER BY o.created_at DESC
    """), {"d": days}).mappings().all()
    ids = [r["id"] for r in rows]
    items = db.execute(text("""
        SELECT ki.order_id, ki.qty, ki.ready_at, mi.name FROM kitchen_order_items ki JOIN menu_items mi ON mi.id = ki.menu_item_id
         WHERE ki.order_id = ANY(:ids) ORDER BY ki.id
    """), {"ids": ids or [0]}).mappings().all()
    out, took = [], []
    for r in rows:
        mins = round((r["packed_at"] - r["created_at"]).total_seconds() / 60, 1) if r["packed_at"] else None
        if r["status"] == "packed" and mins is not None:
            took.append(mins)
        out.append({
            "id": r["id"], "kot": r["kot_no"], "type": r["order_type"], "ref": r["ref"], "by": r["by"],
            "status": r["status"], "day": r["created_at"].astimezone(business_tz()).strftime("%a %d %b"),
            "taken": _clock(r["created_at"]), "packed": _clock(r["packed_at"]) if r["packed_at"] else None,
            "mins": mins, "on_time": mins is not None and mins <= PROMISE_MIN,
            "items": [{"name": i["name"], "qty": i["qty"],
                       "ready_min": round((i["ready_at"] - r["created_at"]).total_seconds() / 60, 1) if i["ready_at"] else None}
                      for i in items if i["order_id"] == r["id"]],
        })
    packed = [o for o in out if o["status"] == "packed"]
    return {"days": days, "orders": out, "summary": {
        "packed": len(packed), "cancelled": sum(1 for o in out if o["status"] == "cancelled"),
        "on_time": sum(1 for o in packed if o["on_time"]),
        "avg_min": round(sum(took) / len(took), 1) if took else None,
        "worst_min": max(took) if took else None, "promise_min": PROMISE_MIN}}
