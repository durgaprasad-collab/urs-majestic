"""Sunday price check helpers (used by the scheduled "sunday-price-check" task).

  python -m scripts.market_prices items  > items.json
      Items bought for the menu in the last 60 days: id, name, unit, category,
      what you last paid per unit and where, and a search hint.

  python -m scripts.market_prices save results.json
      Store the prices found. results.json is a list of
      {"ingredient_id", "channel", "product", "pack_qty", "pack_unit",
       "price", "in_stock"?, "url"?}. pack_unit is kg/g/l/ml/pcs; the price
      per item unit is worked out here. Re-saving the same day replaces it.

  python -m scripts.market_prices summary
      The latest check vs what you pay: cheapest online per item and gaps.
"""
import datetime
import json
import sys

from sqlalchemy import text

from app.core.clock import business_today
from app.core.database import SessionLocal
from app.services.purchases_page import to_item_unit
from app.services import vendor_prices

SKIP_CATEGORIES = {"Overhead", "Staff", "Apparel", "Equipment"}
SKIP_NAMES = {"Cooking Gas", "Vendor delivery charge", "Cashew gravy"}  # cashew gravy is made to order locally
# What to type in a shop's search box when the item name won't find it.
SEARCH = {
    "Ginger-Garlic": "Ginger",  # house convention: fresh ginger is logged here
    "Chinese Garlic": "Garlic", "Gobi": "Cauliflower", "Palak": "Spinach", "Radish/Mullangi": "Radish",
    "Rice/Basmati": "Basmati rice", "Ponni Rice (staff meals)": "Ponni rice",
    "Fresh cream (Rich's Double Dream)": "Rich's whipping cream", "Wheat Flour": "Atta",
    "Monosodium Glutamate (Ajinomoto)": "Ajinomoto", "Cinnamon/Cassia (Dalchini)": "Cinnamon",
    "Clove (Laung)": "Cloves", "Red Chilli (Whole/Dried)": "Dry red chilli", "Jeera Whole (cumin seed)": "Jeera",
    "Black Pepper Powder (ground in-house from whole)": "Black pepper whole",
    "Chicken Masala (veg spice blend)": "Chicken masala", "Briyani essence": "Biryani essence",
    "Oil": "Sunflower oil 15 litre", "Water Can 20L (cooking/drinking)": "20 litre water can",
    "Soap Oil / Dishwash": "Dishwash liquid", "Broom / cleaning tools": "Broom",
    "KOT/Billing Printer Roll 78mm": "Thermal paper roll 78mm",
}


def items(db) -> list[dict]:
    paid = vendor_prices.compare(db, 60)["items"]
    rows = db.execute(text("""
        SELECT DISTINCT i.id, i.name, i.unit::text AS unit, i.category, i.pack_size_g
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.usage_type = 'menu' AND i.is_active
           AND p.purchase_date >= current_date - 60
         ORDER BY i.category NULLS LAST, i.name
    """)).mappings().all()
    out = []
    for r in rows:
        if (r["category"] or "") in SKIP_CATEGORIES or r["name"] in SKIP_NAMES:
            continue
        p = paid.get(r["id"])
        per = {"g": "kg", "kg": "kg", "ml": "litre", "l": "litre"}.get(r["unit"], "piece")
        mult = 1000 if r["unit"] in ("g", "ml") else 1
        out.append({
            "ingredient_id": r["id"], "name": r["name"], "unit": r["unit"], "category": r["category"],
            "search": SEARCH.get(r["name"]) or r["name"].split("(")[0].replace(" - resale", "").strip(),
            "you_pay": ({"per": per, "price": round(p["best"]["last_price"] * mult, 2), "vendor": p["best"]["vendor"],
                         "last_vendor": p["last"]["vendor"], "last_price": round(p["last"]["price"] * mult, 2)} if p else None),
        })
    return out


def save(db, path: str) -> int:
    data = json.load(open(path, encoding="utf-8"))
    units = dict(db.execute(text("SELECT id, unit::text FROM ingredients")).all())
    packs = dict(db.execute(text("SELECT id, pack_size_g FROM ingredients")).all())
    today = business_today()
    keep = []
    for d in data:
        iid = int(d["ingredient_id"])
        if iid not in units:
            continue
        qty = to_item_unit(float(d["pack_qty"]), d["pack_unit"], units[iid], packs[iid])
        if not qty or float(d["price"]) <= 0:
            continue
        keep.append({"d": today, "i": iid, "c": d["channel"].strip(), "p": d["product"].strip()[:300],
                     "q": float(d["pack_qty"]), "u": d["pack_unit"], "pr": float(d["price"]),
                     "ppu": float(d["price"]) / qty, "iu": units[iid], "s": bool(d.get("in_stock", True)),
                     "url": (d.get("url") or None)})
    channels = {k["c"] for k in keep}
    for c in channels:
        db.execute(text("DELETE FROM market_prices WHERE checked_on = :d AND channel = :c"), {"d": today, "c": c})
    for k in keep:
        db.execute(text("""
            INSERT INTO market_prices (checked_on, ingredient_id, channel, product, pack_qty, pack_unit, price,
                                       price_per_unit, item_unit, in_stock, url)
            VALUES (:d, :i, :c, :p, :q, :u, :pr, :ppu, :iu, :s, :url)
        """), k)
    db.commit()
    return len(keep)


def summary(db) -> dict:
    day = db.execute(text("SELECT max(checked_on) FROM market_prices")).scalar()
    if not day:
        return {"checked_on": None, "items": []}
    paid = vendor_prices.compare(db, 60)["items"]
    rows = db.execute(text("""
        SELECT DISTINCT ON (m.ingredient_id) m.ingredient_id, i.name, m.item_unit, m.channel, m.product,
               m.price, m.pack_qty, m.pack_unit, m.price_per_unit
          FROM market_prices m JOIN ingredients i ON i.id = m.ingredient_id
         WHERE m.checked_on = :d AND m.in_stock
         ORDER BY m.ingredient_id, m.price_per_unit
    """), {"d": day}).mappings().all()
    out = []
    for r in rows:
        p = paid.get(r["ingredient_id"])
        mine = p["last"]["price"] if p else None
        out.append({"name": r["name"], "unit": r["item_unit"], "channel": r["channel"], "product": r["product"],
                    "online": float(r["price_per_unit"]), "you_paid": mine,
                    "gap_pct": round((mine - float(r["price_per_unit"])) / mine * 100) if mine else None})
    out.sort(key=lambda x: -(x["gap_pct"] or -999))
    return {"checked_on": day.isoformat(), "items": out}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    db = SessionLocal()
    if cmd == "items":
        print(json.dumps(items(db), ensure_ascii=False, indent=1))
    elif cmd == "save" and len(sys.argv) > 2:
        print(f"saved {save(db, sys.argv[2])} prices")
    elif cmd == "summary":
        print(json.dumps(summary(db), ensure_ascii=False, indent=1, default=str))
    else:
        print(__doc__)
