"""Numbers for the Purchases page: spend, what needs fixing, price moves,
the usual price per item (for the live check while typing a bill) and
recent bills.

Units: a purchase row may be logged in a different unit than the item is
tracked in. kg/g and l/ml convert exactly; a packet/bunch item (pack_size_g)
converts between pieces and weight; sauces convert ml/g 1:1, the same kitchen
approximation the cost engine uses. Anything else (Gobi in pieces with no
weight per head) can't be converted and needs a person -- those are the
"need fixing" rows.
"""
import datetime
import statistics
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_now

_FACTOR = {("kg", "g"): 1000, ("g", "kg"): 0.001, ("l", "ml"): 1000, ("ml", "l"): 0.001,
           ("ml", "g"): 1, ("g", "ml"): 1, ("l", "kg"): 1, ("kg", "l"): 1,
           ("ml", "kg"): 0.001, ("kg", "ml"): 1000, ("l", "g"): 1000, ("g", "l"): 0.001}
PRICE_WINDOW_DAYS = 45
RECENT_DAYS = 7
PRICE_MOVE = 0.15
BILL_DAYS = 14

# SQL twin of to_item_unit() == None, for the sidebar badge.
UNCONVERTIBLE_SQL = """
    p.deleted_at IS NULL AND p.unit::text <> i.unit::text
    AND NOT ((p.unit::text, i.unit::text) IN (('kg','g'),('g','kg'),('l','ml'),('ml','l'),('ml','g'),('g','ml'),
             ('l','kg'),('kg','l'),('ml','kg'),('kg','ml'),('l','g'),('g','l')))
    AND NOT (i.pack_size_g IS NOT NULL AND (p.unit::text IN ('kg','g') AND i.unit::text = 'pcs'
                                             OR p.unit::text = 'pcs' AND i.unit::text IN ('kg','g')))
"""


def to_item_unit(qty: float, unit: str, item_unit: str, pack_g) -> float | None:
    """qty in the item's own unit, or None when it can't be converted."""
    if unit == item_unit:
        return qty
    if (unit, item_unit) in _FACTOR:
        return qty * _FACTOR[(unit, item_unit)]
    if pack_g:
        pack = float(pack_g)
        if item_unit == "pcs" and unit in ("kg", "g"):
            return (qty * 1000 if unit == "kg" else qty) / pack
        if unit == "pcs" and item_unit in ("kg", "g"):
            grams = qty * pack
            return grams / 1000 if item_unit == "kg" else grams
    return None


def _rows(db: Session, since: datetime.date):
    return db.execute(text("""
        SELECT p.id, p.ingredient_id, i.name, i.category, i.unit::text AS item_unit, i.pack_size_g,
               p.qty, p.unit::text AS unit, p.total_price, p.purchase_date, p.usage_type::text AS usage_type
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.purchase_date >= :since
    """), {"since": since}).mappings().all()


def usual_prices(db: Session) -> dict[int, dict]:
    """{ingredient_id: {unit, price (median per item unit), last}} over the price window."""
    today = business_now().date()
    per = defaultdict(list)
    for r in _rows(db, today - datetime.timedelta(days=PRICE_WINDOW_DAYS)):
        if r["usage_type"] != "menu" or not r["total_price"] or r["total_price"] <= 0:
            continue
        q = to_item_unit(float(r["qty"]), r["unit"], r["item_unit"], r["pack_size_g"])
        if q and q > 0:
            per[r["ingredient_id"]].append((r["purchase_date"], float(r["total_price"]) / q, r))
    out = {}
    for iid, ps in per.items():
        ps.sort(key=lambda x: x[0])
        out[iid] = {"unit": ps[-1][2]["item_unit"], "price": statistics.median(p for _, p, _ in ps),
                    "last": ps[-1][1], "last_date": ps[-1][0], "n": len(ps), "name": ps[-1][2]["name"],
                    "category": ps[-1][2]["category"], "series": ps}
    return out


def price_watch(usual: dict[int, dict]) -> list[dict]:
    """Items whose latest price (last RECENT_DAYS) is PRICE_MOVE off the median
    of the purchases before that."""
    today = business_now().date()
    cut = today - datetime.timedelta(days=RECENT_DAYS)
    out = []
    for iid, u in usual.items():
        if u["last_date"] < cut or (u["category"] or "") in ("Utilities", "Overhead", "Staff"):
            continue
        base = [p for d, p, _ in u["series"] if d < cut]
        if len(base) < 3:
            continue
        med = statistics.median(base)
        change = u["last"] / med - 1 if med else 0
        if abs(change) >= PRICE_MOVE:
            out.append({"ingredient_id": iid, "name": u["name"], "unit": u["unit"], "last": u["last"],
                        "usual": med, "change": change * 100})
    out.sort(key=lambda r: -abs(r["change"]))
    return out


def need_fixing(db: Session) -> list[dict]:
    """Live rows whose unit can't be converted to the item's unit, by item."""
    rows = db.execute(text(f"""
        SELECT p.id, p.ingredient_id, i.name, i.unit::text AS item_unit, p.qty, p.unit::text AS unit,
               p.total_price, p.purchase_date, p.row_version
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE {UNCONVERTIBLE_SQL}
         ORDER BY i.name, p.purchase_date DESC
    """)).mappings().all()
    by = {}
    for r in rows:
        g = by.setdefault(r["ingredient_id"], {"ingredient_id": r["ingredient_id"], "name": r["name"],
                                                "item_unit": r["item_unit"], "rows": [], "units": set()})
        g["rows"].append(dict(r))
        g["units"].add(r["unit"])
    out = sorted(by.values(), key=lambda g: -len(g["rows"]))
    for g in out:
        # Pieces of a weighed item can be fixed in one go with a weight per piece.
        g["per_piece"] = g["units"] == {"pcs"} and g["item_unit"] in ("kg", "g")
    return out


def summary(db: Session) -> dict:
    today = business_now().date()
    month_start = today.replace(day=1)
    prev_start = (month_start - datetime.timedelta(days=1)).replace(day=1)
    prev_same = prev_start + datetime.timedelta(days=today.day - 1)
    q = db.execute(text("""
        SELECT coalesce(sum(p.total_price) FILTER (WHERE p.purchase_date >= :ms), 0) AS month,
               coalesce(sum(p.total_price) FILTER (WHERE p.purchase_date BETWEEN :ps AND :pe), 0) AS prev,
               coalesce(sum(p.total_price) FILTER (WHERE p.purchase_date >= :ms AND i.name = 'Cooking Gas'), 0) AS gas,
               coalesce(sum(p.total_price) FILTER (WHERE p.purchase_date > :wk), 0) AS week,
               count(*) FILTER (WHERE p.purchase_date > :wk) AS week_lines
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.purchase_date >= :ps
    """), {"ms": month_start, "ps": prev_start, "pe": prev_same, "wk": today - datetime.timedelta(days=RECENT_DAYS)}).mappings().one()
    cats = db.execute(text("""
        SELECT coalesce(i.category, 'Other') AS cat, sum(p.total_price) AS rs
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.purchase_date >= :ms GROUP BY 1 ORDER BY 2 DESC
    """), {"ms": month_start}).mappings().all()
    people = db.execute(text("""
        SELECT coalesce(u.name, '—') AS name, count(*) AS n
          FROM purchases p LEFT JOIN users u ON u.id = p.entered_by_user_id
         WHERE p.deleted_at IS NULL AND p.purchase_date > :wk GROUP BY 1 ORDER BY 2 DESC
    """), {"wk": today - datetime.timedelta(days=RECENT_DAYS)}).mappings().all()
    top = list(cats[:5])
    rest = sum((c["rs"] for c in cats[5:]), Decimal(0))
    if rest:
        top.append({"cat": "Other", "rs": rest})
    return {**q, "change": (float(q["month"]) / float(q["prev"]) - 1) * 100 if q["prev"] else None,
            "prev_label": f"1–{today.day} {prev_start:%b}", "cats": top, "people": people,
            "month_label": f"{today:%B}"}


def recent_bills(db: Session, days: int = BILL_DAYS) -> list[dict]:
    """Days (newest first), each with its bills: lines grouped by supplier +
    bill number, else by who entered them ("Local purchases")."""
    since = business_now().date() - datetime.timedelta(days=days)
    rows = db.execute(text("""
        SELECT p.id, p.purchase_date, p.vendor, p.bill_ref, p.qty, p.unit::text AS unit, p.total_price,
               p.notes, p.row_version, p.usage_type::text AS usage_type, i.name, i.unit::text AS item_unit,
               i.pack_size_g, coalesce(u.name, '—') AS by_name, p.created_at
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
          LEFT JOIN users u ON u.id = p.entered_by_user_id
         WHERE p.deleted_at IS NULL AND p.purchase_date >= :since
         ORDER BY p.purchase_date DESC, p.created_at
    """), {"since": since}).mappings().all()
    days_out: dict[datetime.date, dict] = {}
    for r in rows:
        d = days_out.setdefault(r["purchase_date"], {"date": r["purchase_date"], "bills": {}, "total": Decimal(0)})
        key = (r["vendor"] or "", r["bill_ref"] or "", "" if r["vendor"] else r["by_name"])
        b = d["bills"].setdefault(key, {"vendor": r["vendor"] or "Local purchases", "bill_ref": r["bill_ref"],
                                        "people": set(), "lines": [], "total": Decimal(0)})
        line = dict(r)
        line["unconvertible"] = to_item_unit(float(r["qty"]), r["unit"], r["item_unit"], r["pack_size_g"]) is None
        b["lines"].append(line)
        b["people"].add(r["by_name"])
        b["total"] += r["total_price"] or 0
        d["total"] += r["total_price"] or 0
    out = []
    for d in days_out.values():
        bills = sorted(d["bills"].values(), key=lambda b: -len(b["lines"]))
        for b in bills:
            b["people"] = ", ".join(sorted(b["people"]))
            b["issues"] = sum(1 for l in b["lines"] if l["unconvertible"])
        out.append({"date": d["date"], "total": d["total"], "bills": bills})
    return out


def vendors(db: Session) -> list[str]:
    return [r[0] for r in db.execute(text("""
        SELECT vendor FROM purchases WHERE vendor IS NOT NULL AND deleted_at IS NULL
         GROUP BY vendor ORDER BY max(purchase_date) DESC LIMIT 30
    """))]
