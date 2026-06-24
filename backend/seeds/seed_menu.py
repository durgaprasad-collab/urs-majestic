"""
Seed canonical menu items and POS aliases for URS Majestic.
Idempotent: upserts by name, skips existing aliases.

Usage (from backend/ with venv active):
    python -m seeds.seed_menu
"""

import sys
import os

# Allow running from project root or backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before importing app modules
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from decimal import Decimal
from app.core.database import SessionLocal
from app.models.menu_item import MenuItem, PosAlias

# ── Canonical menu items ──────────────────────────────────────────────────────

MENU_ITEMS = [
    # Soups
    {"name": "Tomato Soup",       "category": "Soups", "price": 79,  "is_food": True},
    {"name": "Mushroom Soup",     "category": "Soups", "price": 79,  "is_food": True},
    {"name": "Hot & Sour Soup",   "category": "Soups", "price": 79,  "is_food": True},
    {"name": "Manchow Soup",      "category": "Soups", "price": 79,  "is_food": True},
    {"name": "Sweet Corn Soup",   "category": "Soups", "price": 79,  "is_food": True},

    # Starters-65
    {"name": "Gobi 65",      "category": "Starters-65", "price": 99,  "is_food": True},
    {"name": "Mushroom 65",  "category": "Starters-65", "price": 99,  "is_food": True},
    {"name": "Baby Corn 65", "category": "Starters-65", "price": 99,  "is_food": True},
    {"name": "Paneer 65",    "category": "Starters-65", "price": 129, "is_food": True},

    # Starters-Manchurian
    {"name": "Veg Manchurian",                "category": "Starters-Manchurian", "price": 129, "is_food": True},
    {"name": "Gobi Manchurian (Starter)",     "category": "Starters-Manchurian", "price": 129, "is_food": True},
    {"name": "Mushroom Manchurian (Starter)", "category": "Starters-Manchurian", "price": 129, "is_food": True},
    {"name": "Baby Corn Manchurian (Starter)","category": "Starters-Manchurian", "price": 129, "is_food": True},
    {"name": "Mushroom Pepper Fry",           "category": "Starters-Manchurian", "price": 139, "is_food": True},
    {"name": "Paneer Manchurian (Starter)",   "category": "Starters-Manchurian", "price": 139, "is_food": True},

    # Starters-Chilli
    {"name": "Chilli Mushroom",  "category": "Starters-Chilli", "price": 119, "is_food": True},
    {"name": "Chilli Baby Corn", "category": "Starters-Chilli", "price": 119, "is_food": True},
    {"name": "Crispy Veg",       "category": "Starters-Chilli", "price": 129, "is_food": True},
    {"name": "Chilli Gobi",      "category": "Starters-Chilli", "price": 129, "is_food": True},
    {"name": "Chilli Paneer",    "category": "Starters-Chilli", "price": 149, "is_food": True},

    # Breads
    {"name": "Phulka (2 Nos)",                    "category": "Breads", "price": 29,  "is_food": True},
    {"name": "Stuffed Paratha (Aloo/Gobi) (1 No)","category": "Breads", "price": 59,  "is_food": True},
    {"name": "Paneer Paratha (1 No)",             "category": "Breads", "price": 79,  "is_food": True},
    {"name": "Chappati (2) With Channa",          "category": "Breads", "price": 79,  "is_food": True},
    {"name": "Channa Batura",                     "category": "Breads", "price": 99,  "is_food": True},
    {"name": "Chilli Parotta",                    "category": "Breads", "price": 149, "is_food": True},

    # Tikka
    {"name": "Mushroom Tikka",       "category": "Tikka", "price": 149, "is_food": True},
    {"name": "Paneer Tikka (5 Pcs)", "category": "Tikka", "price": 179, "is_food": True},

    # Tandoori Breads
    {"name": "Roti (2 Nos)",       "category": "Tandoori Breads", "price": 49, "is_food": True},
    {"name": "Butter Roti (2 Nos)","category": "Tandoori Breads", "price": 59, "is_food": True},
    {"name": "Naan (1 No)",        "category": "Tandoori Breads", "price": 49, "is_food": True},
    {"name": "Butter Naan (1 No)", "category": "Tandoori Breads", "price": 59, "is_food": True},
    {"name": "Garlic Naan (1 No)", "category": "Tandoori Breads", "price": 69, "is_food": True},
    {"name": "Masala Kulcha (1 No)","category": "Tandoori Breads","price": 99, "is_food": True},

    # Fried Rice / Noodles
    {"name": "Veg Fried Rice",          "category": "Fried Rice / Noodles", "price": 129, "is_food": True},
    {"name": "Gobi Fried Rice",         "category": "Fried Rice / Noodles", "price": 149, "is_food": True},
    {"name": "Mushroom Fried Rice",     "category": "Fried Rice / Noodles", "price": 149, "is_food": True},
    {"name": "Paneer Fried Rice",       "category": "Fried Rice / Noodles", "price": 159, "is_food": True},
    {"name": "Chilli Garlic Fried Rice","category": "Fried Rice / Noodles", "price": 149, "is_food": True},
    {"name": "Mixed Fried Rice",        "category": "Fried Rice / Noodles", "price": 179, "is_food": True},

    # Rice / Briyanis
    {"name": "Jeera Rice",              "category": "Rice / Briyanis", "price": 109, "is_food": True},
    {"name": "Veg Briyani / Pulav",     "category": "Rice / Briyanis", "price": 149, "is_food": True},
    {"name": "Mushroom Briyani / Pulav","category": "Rice / Briyanis", "price": 169, "is_food": True},
    {"name": "Paneer Briyani / Pulav",  "category": "Rice / Briyanis", "price": 169, "is_food": True},

    # Gravy-Veg/Dal
    {"name": "Dal Tadka / Fry", "category": "Gravy-Veg/Dal", "price": 99,  "is_food": True},
    {"name": "Channa Masala",   "category": "Gravy-Veg/Dal", "price": 129, "is_food": True},
    {"name": "Kadai Veg",       "category": "Gravy-Veg/Dal", "price": 149, "is_food": True},
    {"name": "Mixed Veg",       "category": "Gravy-Veg/Dal", "price": 149, "is_food": True},

    # Gravy-Baby Corn
    {"name": "Baby Corn Masala",           "category": "Gravy-Baby Corn", "price": 129, "is_food": True},
    {"name": "Baby Corn Manchurian (Gravy)","category": "Gravy-Baby Corn","price": 149, "is_food": True},

    # Gravy-Gobi
    {"name": "Gobi Masala",            "category": "Gravy-Gobi", "price": 129, "is_food": True},
    {"name": "Aloo Gobi Masala",       "category": "Gravy-Gobi", "price": 129, "is_food": True},
    {"name": "Gobi Manchurian (Gravy)","category": "Gravy-Gobi", "price": 149, "is_food": True},

    # Gravy-Mushroom
    {"name": "Mushroom Masala",            "category": "Gravy-Mushroom", "price": 129, "is_food": True},
    {"name": "Mushroom Manchurian (Gravy)","category": "Gravy-Mushroom", "price": 149, "is_food": True},
    {"name": "Kadai Mushroom",             "category": "Gravy-Mushroom", "price": 149, "is_food": True},

    # Gravy-Paneer
    {"name": "Paneer Manchurian (Gravy)","category": "Gravy-Paneer", "price": 149, "is_food": True},
    {"name": "Paneer Butter Masala",    "category": "Gravy-Paneer", "price": 179, "is_food": True},
    {"name": "Matar Paneer",            "category": "Gravy-Paneer", "price": 179, "is_food": True},
    {"name": "Hyderabadi Paneer",       "category": "Gravy-Paneer", "price": 179, "is_food": True},
    {"name": "Kadai Paneer",            "category": "Gravy-Paneer", "price": 179, "is_food": True},
    {"name": "Paneer Tikka Masala",     "category": "Gravy-Paneer", "price": 199, "is_food": True},

    # Combos
    {"name": "Combo 01 - Mini North Indian Meal",       "category": "Combos", "price": 149, "is_food": True},
    {"name": "Combo 02 - Paneer Power Combo",           "category": "Combos", "price": 149, "is_food": True},
    {"name": "Combo 03 - Veg Kofta Combo",              "category": "Combos", "price": 149, "is_food": True},
    {"name": "Combo 04 - Soup & Starter Combo",         "category": "Combos", "price": 149, "is_food": True},
    {"name": "Combo 05 - Chinese Mega Box",             "category": "Combos", "price": 249, "is_food": True},
    {"name": "Combo 06a - Briyani Express (Veg)",       "category": "Combos", "price": 199, "is_food": True},
    {"name": "Combo 06b - Briyani Express (Mushroom)",  "category": "Combos", "price": 229, "is_food": True},
    {"name": "Combo 06c - Briyani Express (Paneer)",    "category": "Combos", "price": 249, "is_food": True},

    # Non-Food
    {"name": "Water Bottle", "category": "Non-Food", "price": 20, "is_food": False},
    {"name": "Goli Soda",    "category": "Non-Food", "price": 50, "is_food": False},
    {"name": "Parcel",       "category": "Non-Food", "price": 10, "is_food": False},
]

# ── POS name → canonical menu item name ──────────────────────────────────────

POS_NAME_MAP = {
    "Butter Naan Butter Masala Paneer 65 (5 Pcs)":           "Combo 02 - Paneer Power Combo",
    "Roti, Dal Tadka Jeera Rice, Paneer Subzi S Al Ad":      "Combo 01 - Mini North Indian Meal",
    "Veg Kofta 4 Pcs (2 Roti)":                             "Combo 03 - Veg Kofta Combo",
    "Mushroom Briyani, Raita, Mushroom 65 (5 Pcs)":         "Combo 06b - Briyani Express (Mushroom)",
    "Veg Briyani, Raita , Gobi 65 (5 P Cs)":               "Combo 06a - Briyani Express (Veg)",
    "Mushroom Chilli":                                        "Chilli Mushroom",
    "Mushroom Biriyani":                                      "Mushroom Briyani / Pulav",
    "Mush. Briyani":                                         "Mushroom Briyani / Pulav",
    "Veg Briyani":                                           "Veg Briyani / Pulav",
    "Veg Pulav":                                             "Veg Briyani / Pulav",
    "Paneer Briyani":                                        "Paneer Briyani / Pulav",
    "Paneer Pulav":                                          "Paneer Briyani / Pulav",
    "Chappati With Channa":                                  "Chappati (2) With Channa",
    "Paneer Paratha":                                        "Paneer Paratha (1 No)",
    "Gobi Fried Rice (Regular)":                             "Gobi Fried Rice",
    "Mushroom Fried Rice (Regular)":                         "Mushroom Fried Rice",
    "Veg Fried Rice (Regular)":                              "Veg Fried Rice",
    "Mixed (veg,gobi,mushroom & Paneer) Fried Rice (Regular)": "Mixed Fried Rice",
    "Chilli Garlic Fried Rice (price)":                      "Chilli Garlic Fried Rice",
    "Gobi Manchurian":                                       "Gobi Manchurian (Starter)",
    "Mushroom Manchurian":                                   "Mushroom Manchurian (Starter)",
    "Baby Corn Manchurian":                                  "Baby Corn Manchurian (Starter)",
    "Paneer Manchurian":                                     "Paneer Manchurian (Starter)",
}


# ── Seeding logic ─────────────────────────────────────────────────────────────

def seed():
    db = SessionLocal()
    try:
        inserted = updated = alias_added = alias_skipped = 0

        # Build lookup of existing items by name
        existing: dict[str, MenuItem] = {
            m.name: m for m in db.query(MenuItem).all()
        }

        for row in MENU_ITEMS:
            name = row["name"]
            if name in existing:
                item = existing[name]
                changed = False
                for field in ("category", "is_food"):
                    if getattr(item, field) != row[field]:
                        setattr(item, field, row[field])
                        changed = True
                price = Decimal(str(row["price"]))
                if item.price != price:
                    item.price = price
                    changed = True
                if changed:
                    updated += 1
            else:
                item = MenuItem(
                    name=name,
                    category=row["category"],
                    price=Decimal(str(row["price"])),
                    is_active=True,
                    is_food=row["is_food"],
                )
                db.add(item)
                db.flush()  # get id before aliases
                existing[name] = item
                inserted += 1

        db.flush()

        # Reload existing aliases once
        existing_aliases: set[str] = {
            a.pos_name for a in db.query(PosAlias).all()
        }

        for pos_name, canonical_name in POS_NAME_MAP.items():
            if pos_name in existing_aliases:
                alias_skipped += 1
                continue
            item = existing.get(canonical_name)
            if item is None:
                print(f"  [WARN] No menu item found for canonical name: {canonical_name!r}")
                continue
            db.add(PosAlias(menu_item_id=item.id, pos_name=pos_name))
            alias_added += 1

        db.commit()
        print(f"  Menu items:  {inserted} inserted, {updated} updated")
        print(f"  POS aliases: {alias_added} added, {alias_skipped} already existed")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("\nSeeding URS Majestic menu...")
    seed()
    print("Done.\n")
