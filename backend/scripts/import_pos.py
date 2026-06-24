"""
POS sales importer for URS Majestic.

Steps:
  1. Upserts menu_items from backend/data/menu_seed.json (canonical source of truth).
  2. Parses backend/data/pos_export.xlsx (Item | Date | Qty | Total columns).
  3. Resolves each POS name to a canonical menu item via pos_name_map then exact match.
  4. Inserts resolved rows into item_sales (truncates first — fully reloadable).
  5. Prints a summary.

Usage (from backend/ with venv active):
    python -m scripts.import_pos
    python -m scripts.import_pos --xlsx path/to/other.xlsx
"""

import sys
import os
import argparse
import json
from datetime import date
from decimal import Decimal

# Allow running from project root or backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.core.database import SessionLocal
from app.models.menu_item import MenuItem, PosAlias
from app.models.item_sale import ItemSale

SEED_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "menu_seed.json")
DEFAULT_XLSX = os.path.join(os.path.dirname(__file__), "..", "data", "pos_export.xlsx")


# -- Step 1: Seed menu items ------------------------------------------─────────────────

def seed_menu_items(db, seed: dict) -> dict[str, MenuItem]:
    """Upsert menu items from seed data. Returns name→MenuItem map."""
    food_cost_default = Decimal(str(seed["_meta"]["food_cost_pct_default"]))
    existing: dict[str, MenuItem] = {m.name: m for m in db.query(MenuItem).all()}
    inserted = updated = 0

    for row in seed["items"]:
        name = row["name"]
        price = Decimal(str(row["price"]))
        if name in existing:
            item = existing[name]
            changed = False
            for field, val in [("category", row["category"]), ("is_food", row["is_food"])]:
                if getattr(item, field) != val:
                    setattr(item, field, val)
                    changed = True
            if item.price != price:
                item.price = price
                changed = True
            if item.food_cost_pct != food_cost_default:
                item.food_cost_pct = food_cost_default
                changed = True
            if changed:
                updated += 1
        else:
            item = MenuItem(
                name=name,
                category=row["category"],
                price=price,
                is_active=True,
                is_food=row["is_food"],
                food_cost_pct=food_cost_default,
            )
            db.add(item)
            db.flush()
            existing[name] = item
            inserted += 1

    # Upsert POS aliases
    existing_aliases: set[str] = {a.pos_name for a in db.query(PosAlias).all()}
    alias_added = 0
    for pos_name, canonical in seed.get("pos_name_map", {}).items():
        if pos_name not in existing_aliases:
            item = existing.get(canonical)
            if item:
                db.add(PosAlias(menu_item_id=item.id, pos_name=pos_name))
                alias_added += 1

    db.flush()
    print(f"  [menu_items]  {inserted} inserted, {updated} updated")
    print(f"  [pos_aliases] {alias_added} added")
    return existing


# ── Step 2: Parse xlsx ────────────────────────────────────────────────────────

def parse_xlsx(path: str) -> list[dict]:
    """Parse POS xlsx. Returns list of {raw_name, sale_date, qty, revenue}."""
    try:
        import openpyxl
    except ImportError:
        print("  ERROR: openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # Find header row: look for a row where col A contains "Item" (case-insensitive)
    header_row = None
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if row[0] and str(row[0]).strip().lower() == "item":
            header_row = i
            break

    if header_row is None:
        raise ValueError("Could not find header row with 'Item' in column A")

    rows = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        raw_name = row[0]
        if raw_name is None:
            continue
        raw_name = str(raw_name).strip()
        if not raw_name or raw_name.lower() in ("total", "grand total", ""):
            continue

        date_val = row[1]
        qty_val = row[2]
        revenue_val = row[3]

        if date_val is None or qty_val is None or revenue_val is None:
            continue

        # Normalize date
        if isinstance(date_val, (date,)):
            sale_date = date_val
        else:
            from datetime import datetime as dt
            if hasattr(date_val, "date"):
                sale_date = date_val.date()
            else:
                try:
                    sale_date = dt.strptime(str(date_val).strip(), "%d-%m-%Y").date()
                except ValueError:
                    try:
                        sale_date = dt.strptime(str(date_val).strip(), "%Y-%m-%d").date()
                    except ValueError:
                        print(f"  [WARN] Could not parse date: {date_val!r} for item {raw_name!r}")
                        continue

        try:
            qty = Decimal(str(qty_val))
            revenue = Decimal(str(revenue_val))
        except Exception:
            continue

        rows.append({
            "raw_name": raw_name,
            "sale_date": sale_date,
            "qty": qty,
            "revenue": revenue,
        })

    return rows


# ── Step 3: Resolve names ─────────────────────────────────────────────────────

def build_resolver(seed: dict, menu_map: dict[str, MenuItem]) -> dict[str, str]:
    """Returns pos_name → canonical_name mapping for all known names."""
    resolver: dict[str, str] = {}
    # From pos_name_map
    for pos_name, canonical in seed.get("pos_name_map", {}).items():
        resolver[pos_name] = canonical
    # Direct exact matches
    for name in menu_map:
        if name not in resolver:
            resolver[name] = name
    return resolver


# ── Step 4: Insert item_sales ─────────────────────────────────────────────────

def load_sales(db, rows: list[dict], resolver: dict[str, str]) -> tuple[list, list]:
    matched = []
    unmatched = []

    for row in rows:
        canonical = resolver.get(row["raw_name"])
        if canonical is None:
            unmatched.append(row["raw_name"])
        else:
            matched.append(ItemSale(
                raw_name=row["raw_name"],
                item_name=canonical,
                sale_date=row["sale_date"],
                qty=row["qty"],
                revenue=row["revenue"],
            ))

    # Truncate and reload for idempotency
    db.query(ItemSale).delete()
    db.bulk_save_objects(matched)
    db.flush()

    return matched, unmatched


# ── Main ──────────────────────────────────────────────────────────────────────

def main(xlsx_path: str):
    with open(SEED_JSON) as f:
        seed = json.load(f)

    db = SessionLocal()
    try:
        print("\n-- Step 1: Seed menu items ------------------------------------------")
        menu_map = seed_menu_items(db, seed)

        print("\n-- Step 2: Parse POS xlsx -------------------------------------------")
        raw_rows = parse_xlsx(xlsx_path)
        print(f"  {len(raw_rows)} data rows read from {os.path.basename(xlsx_path)}")

        print("\n-- Step 3: Resolve and insert item_sales ----------------------------")
        resolver = build_resolver(seed, menu_map)
        matched, unmatched = load_sales(db, raw_rows, resolver)
        db.commit()

        # ── Summary ───────────────────────────────────────────────────────────
        dates = [r.sale_date for r in matched]
        total_rev = sum(r.revenue for r in matched)
        distinct_items = len({r.item_name for r in matched})
        unmatched_unique = sorted(set(unmatched))

        print("\n==============================================================")
        print("  IMPORT SUMMARY")
        print("==============================================================")
        print(f"  Rows matched   : {len(matched)}")
        print(f"  Rows unmatched : {len(unmatched)}")
        print(f"  Distinct items : {distinct_items}")
        print(f"  Total revenue  : ₹{total_rev:,.2f}")
        if dates:
            print(f"  Date range     : {min(dates)} → {max(dates)}")
        if unmatched_unique:
            print(f"\n  Unmatched POS names ({len(unmatched_unique)}):")
            for n in unmatched_unique:
                print(f"    • {n}")
        print("==============================================================\n")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import POS sales xlsx into ursmajestic DB")
    parser.add_argument("--xlsx", default=DEFAULT_XLSX, help="Path to POS xlsx export")
    args = parser.parse_args()

    if not os.path.exists(args.xlsx):
        print(f"ERROR: xlsx not found at {args.xlsx}")
        print("Place your POS export at backend/data/pos_export.xlsx or pass --xlsx <path>")
        sys.exit(1)

    main(args.xlsx)
