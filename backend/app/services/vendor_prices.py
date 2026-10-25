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
        it["last"] = {"vendor": vendor, "price": price, "date": r["purchase_date"], "qty": q}
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


SMALL_PACK = 0.25  # a pack under this share of what you usually buy is a "small pack": dearer per unit, not a fair comparison


def market_offers(db: Session, items: dict | None = None) -> dict:
    """Every app's offer for each item from the latest price check:
    {ingredient_id: {"day", "unit", "offers": [..]}} (+ "_day"). Offers are
    ordered fair-comparison first (cheapest per unit), then small packs, then
    out of stock. `small` = the pack is under SMALL_PACK of the last quantity
    bought; `approx` = the pack is in a different unit family than the item
    (ml vs g, assumed 1:1)."""
    day = db.execute(text("SELECT max(checked_on) FROM market_prices")).scalar()
    if not day:
        return {}
    items = items if items is not None else compare(db)["items"]
    last_qty = {iid: it["last"]["qty"] for iid, it in items.items() if it["last"]}
    paid = {iid: it["last"]["price"] for iid, it in items.items() if it["last"]}
    rows = db.execute(text("""
        SELECT m.ingredient_id, m.channel, m.product, m.pack_qty, m.pack_unit, m.price, m.price_per_unit, m.in_stock, m.url,
               i.unit::text AS item_unit, i.pack_size_g
          FROM market_prices m JOIN ingredients i ON i.id = m.ingredient_id WHERE m.checked_on = :d
    """), {"d": day}).mappings().all()
    out: dict = {}
    for r in rows:
        iid = r["ingredient_id"]
        pack = to_item_unit(float(r["pack_qty"]), r["pack_unit"], r["item_unit"], r["pack_size_g"])
        ppu = float(r["price_per_unit"])
        family = lambda u: "w" if u in ("kg", "g") else ("v" if u in ("l", "ml") else "p")  # noqa: E731
        small = bool(pack and last_qty.get(iid) and pack < last_qty[iid] * SMALL_PACK)
        o = {"channel": r["channel"], "product": r["product"], "pack_qty": float(r["pack_qty"]), "pack_unit": r["pack_unit"],
             "price": float(r["price"]), "ppu": ppu, "in_stock": bool(r["in_stock"]), "url": r["url"], "small": small,
             "approx": family(r["pack_unit"]) != family(r["item_unit"]),
             "vs_paid": (ppu / paid[iid] - 1) * 100 if paid.get(iid) else None}
        out.setdefault(iid, {"day": day, "unit": r["item_unit"], "offers": []})["offers"].append(o)
    for v in out.values():
        v["offers"].sort(key=lambda o: (not o["in_stock"], o["small"], o["ppu"]))
        first = next((o for o in v["offers"] if o["in_stock"] and not o["small"]), None)
        if first:
            first["best"] = True
        v["n_fair"] = sum(1 for o in v["offers"] if o["in_stock"] and not o["small"])
    out["_day"] = day
    return out


def market_latest(db: Session, offers: dict | None = None) -> dict:
    """The latest check: {ingredient_id: cheapest fair in-stock offer} (a small
    pack only when nothing else is in stock), plus {"_day": date}; empty
    before the first check."""
    offers = offers if offers is not None else market_offers(db)
    if not offers:
        return {}
    out: dict = {"_day": offers["_day"]}
    for iid, v in offers.items():
        if iid == "_day":
            continue
        pick = next((o for o in v["offers"] if o["in_stock"] and not o["small"]), None) or next((o for o in v["offers"] if o["in_stock"]), None)
        if pick:
            out[iid] = {"channel": pick["channel"], "product": pick["product"], "price": pick["price"], "pack_qty": pick["pack_qty"],
                        "pack_unit": pick["pack_unit"], "price_per_unit": pick["ppu"], "n": v["n_fair"], "day": v["day"], "small": pick["small"]}
    return out


def page(db: Session, days: int = DAYS) -> dict:
    data = compare(db, days)
    offers = market_offers(db, data["items"])
    market = market_latest(db, offers)
    for it in data["items"].values():
        it["online"] = market.get(it["id"])
        it["apps"] = offers.get(it["id"])
    rows = sorted((it for it in data["items"].values() if it["n_vendors"] > 1), key=lambda it: -it["over"])
    single = sorted((it for it in data["items"].values() if it["n_vendors"] == 1), key=lambda it: it["name"].lower())
    return {
        "rows": rows, "single": single, "days": days, "unnamed": data["unnamed"],
        "over_total": sum(it["over"] for it in rows),
        "top": rows[:2],
        "categories": sorted({it["category"] or "Other" for it in rows}),
        "market_day": market.get("_day"),
        "cheaper_online": sorted((it for it in data["items"].values() if it.get("online") and not it["online"]["small"] and it["last"]
                                  and it["online"]["price_per_unit"] < it["last"]["price"] * 0.9),
                                 key=lambda it: it["online"]["price_per_unit"] / it["last"]["price"]),
    }
