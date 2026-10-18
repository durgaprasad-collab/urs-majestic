"""Vendor prices: what each vendor charges per item, in the item's own unit
(per kg / litre / piece), so a 500 g pack and a 5 kg sack compare fairly.

Used by the Buy page (one "cheapest lately" line per item) and the Vendor
prices page under Purchases. Delivery and handling lines are their own
purchases and aren't spread over the items, so a cheap quick-commerce price
can still cost more once the fee is added: "paid over cheapest" is a ceiling.
"""
import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.services.purchases_page import to_item_unit

DAYS = 60
NO_VENDOR = ("", "cash memo")  # not a vendor you can compare against
SKIP_CATEGORIES = {"Overhead", "Staff"}
DEAR = 1.5  # last bought at this many times the cheapest -> say so


def _lines(db: Session, since: datetime.date):
    return db.execute(text("""
        SELECT p.ingredient_id, i.name, i.category, i.unit::text AS item_unit, i.pack_size_g,
               p.vendor, p.qty, p.unit::text AS unit, p.total_price, p.purchase_date, p.created_at
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.usage_type = 'menu' AND p.qty > 0 AND p.total_price > 0
           AND p.purchase_date >= :since AND i.is_active
         ORDER BY p.purchase_date, p.created_at
    """), {"since": since}).mappings().all()


def compare(db: Session, days: int = DAYS) -> dict:
    """{items: {ingredient_id: {...}}, unnamed: {n, spend}} for the last `days`."""
    since = business_today() - datetime.timedelta(days=days)
    items: dict[int, dict] = {}
    unnamed_n, unnamed_rs = 0, 0.0
    for r in _lines(db, since):
        if (r["category"] or "") in SKIP_CATEGORIES:
            continue
        vendor = (r["vendor"] or "").strip()
        if vendor.lower() in NO_VENDOR:
            unnamed_n += 1
            unnamed_rs += float(r["total_price"])
            continue
        q = to_item_unit(float(r["qty"]), r["unit"], r["item_unit"], r["pack_size_g"])
        if not q:
            continue
        it = items.setdefault(r["ingredient_id"], {"id": r["ingredient_id"], "name": r["name"], "category": r["category"],
                                                   "unit": r["item_unit"], "vendors": {}, "last": None})
        v = it["vendors"].setdefault(vendor, {"vendor": vendor, "qty": 0.0, "spend": 0.0, "n": 0})
        price = float(r["total_price"]) / q
        v["qty"] += q
        v["spend"] += float(r["total_price"])
        v["n"] += 1
        v["last_price"], v["last_date"] = price, r["purchase_date"]
        it["last"] = {"vendor": vendor, "price": price, "date": r["purchase_date"]}
    for it in items.values():
        vs = list(it["vendors"].values())
        total_qty = sum(v["qty"] for v in vs)
        for v in vs:
            v["avg"] = v["spend"] / v["qty"]
            v["share"] = v["qty"] / total_qty * 100 if total_qty else 0
        it["vendors"] = sorted(vs, key=lambda v: v["last_price"])  # cheapest lately first
        it["n_vendors"] = len(vs)
        cheapest_avg = min(v["avg"] for v in vs)
        it["over"] = sum((v["avg"] - cheapest_avg) * v["qty"] for v in vs)
        best = it["vendors"][0]
        it["best"] = best
        last = it["last"]
        it["dear"] = (last["price"] / best["last_price"]) if (len(vs) > 1 and last["vendor"] != best["vendor"]
                                                            and best["last_price"] and last["price"] >= best["last_price"] * DEAR) else None
        it["top_share"] = max(vs, key=lambda v: v["share"])
    return {"items": items, "unnamed": {"n": unnamed_n, "spend": unnamed_rs}, "days": days}


def market_latest(db: Session) -> dict:
    """The latest Sunday price check: {ingredient_id: cheapest in-stock offer},
    plus {"_day": date} -- empty before the first check."""
    day = db.execute(text("SELECT max(checked_on) FROM market_prices")).scalar()
    if not day:
        return {}
    rows = db.execute(text("""
        SELECT DISTINCT ON (ingredient_id) ingredient_id, channel, product, price, pack_qty, pack_unit, price_per_unit,
               (SELECT count(DISTINCT channel) FROM market_prices m2
                 WHERE m2.ingredient_id = m.ingredient_id AND m2.checked_on = m.checked_on AND m2.in_stock) AS n
          FROM market_prices m WHERE checked_on = :d AND in_stock
         ORDER BY ingredient_id, price_per_unit
    """), {"d": day}).mappings().all()
    out = {r["ingredient_id"]: {**dict(r), "price_per_unit": float(r["price_per_unit"]), "day": day} for r in rows}
    out["_day"] = day
    return out


def page(db: Session, days: int = DAYS) -> dict:
    data = compare(db, days)
    market = market_latest(db)
    for it in data["items"].values():
        it["online"] = market.get(it["id"])
    rows = sorted((it for it in data["items"].values() if it["n_vendors"] > 1), key=lambda it: -it["over"])
    single = sorted((it for it in data["items"].values() if it["n_vendors"] == 1), key=lambda it: it["name"].lower())
    return {
        "rows": rows, "single": single, "days": days, "unnamed": data["unnamed"],
        "over_total": sum(it["over"] for it in rows),
        "top": rows[:2],
        "categories": sorted({it["category"] or "Other" for it in rows}),
        "market_day": market.get("_day"),
        "cheaper_online": sorted((it for it in data["items"].values() if it.get("online") and it["last"]
                                  and it["online"]["price_per_unit"] < it["last"]["price"] * 0.9),
                                 key=lambda it: it["online"]["price_per_unit"] / it["last"]["price"]),
    }
