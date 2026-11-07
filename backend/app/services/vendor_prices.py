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


SITE_ORDER = ["Hyperpure", "Instamart", "Blinkit", "BigBasket", "Zepto"]  # columns on the compare page, in this order


def _site(channel: str) -> str:
    c = (channel or "").strip()
    low = c.lower()
    if "instamart" in low:
        return "Instamart"
    if "bigbasket" in low or "bbnow" in low or "big basket" in low:
        return "BigBasket"
    return c


def compare_matrix(db: Session) -> dict:
    """One row per item: a cell per delivery site (latest price check) and a
    cell per local shop (last price you paid there, from purchase bills).
    {day, sites, shops, rows: [{id, name, category, unit, paid, cells, shop_cells, cheapest: [keys]}]}.
    The cheapest is the lowest price per unit among in-stock, normal-size
    online packs and local-shop prices; small packs count only when nothing
    else is available. Local-shop keys are prefixed "shop:"."""
    items = compare(db)["items"]
    offers = market_offers(db, items)
    online_words = ("hyperpure", "instamart", "blinkit", "bigbasket", "big basket", "bbnow", "zepto", "zomato", "swiggy",
                    "jiomart", "amazon", "flipkart")  # delivery apps, not local shops
    rows, seen, shop_n = [], set(), {}
    for iid, it in items.items():
        v = offers.get(iid) if offers else None
        cells: dict = {}
        for o in (v["offers"] if v else []):  # already ordered best first
            cells.setdefault(_site(o["channel"]), o)
            seen.add(_site(o["channel"]))
        shop_cells: dict = {}
        for vd in it["vendors"]:
            if any(w in vd["vendor"].lower() for w in online_words):
                continue
            shop_cells[vd["vendor"]] = {"ppu": vd["last_price"], "date": vd["last_date"], "n": vd["n"], "avg": vd["avg"]}
            shop_n[vd["vendor"]] = shop_n.get(vd["vendor"], 0) + 1
        if not cells and not shop_cells:
            continue
        pool = [(s, o["ppu"]) for s, o in cells.items() if o["in_stock"] and not o["small"]]             or [(s, o["ppu"]) for s, o in cells.items() if o["in_stock"]]
        pool += [("shop:" + s, c["ppu"]) for s, c in shop_cells.items()]
        low = min((p for _, p in pool), default=None)
        cheapest = [k for k, p in pool if low is not None and p <= low * 1.0001]
        for o in cells.values():
            o["vs_cheapest"] = (o["ppu"] / low - 1) * 100 if low and o["in_stock"] else None
        rows.append({"id": iid, "name": it["name"], "category": it["category"] or "Other", "unit": it["unit"],
                     "paid": ({"price": it["last"]["price"], "vendor": it["last"]["vendor"], "date": it["last"]["date"]}
                              if it["last"] else None),
                     "cells": cells, "shop_cells": shop_cells, "cheapest": cheapest, "low": low,
                     "saving": ((it["last"]["price"] - low) / it["last"]["price"] * 100
                                if low and it["last"] and it["last"]["price"] else None)})
    rows.sort(key=lambda r: r["name"].lower())
    sites = [s for s in SITE_ORDER if s in seen] + sorted(s for s in seen if s not in SITE_ORDER)
    shops = sorted(shop_n, key=lambda s: (-shop_n[s], s.lower()))
    return {"day": offers["_day"] if offers else None, "sites": sites, "shops": shops, "rows": rows,
            "categories": sorted({r["category"] for r in rows})}


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
