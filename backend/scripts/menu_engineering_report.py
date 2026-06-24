"""
Menu engineering CLI report for URS Majestic.

Reads item_sales + menu_items from the DB and prints a formatted
Stars / Workhorses / Puzzles / Dogs breakdown.

Usage (from backend/ with venv active):
    python -m scripts.menu_engineering_report
    python -m scripts.menu_engineering_report --min-units 5
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env):
    with open(_env) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.core.database import SessionLocal
from app.services.menu_engineering.analysis import get_analysis

CLASS_ORDER = ["Star", "Workhorse", "Puzzle", "Dog"]
CLASS_LABEL = {
    "Star":      "STARS      (high vol, high margin)  -- feature & upsell",
    "Workhorse": "WORKHORSES (high vol, low margin)   -- raise price or bundle",
    "Puzzle":    "PUZZLES    (low vol, high margin)   -- promote / reposition",
    "Dog":       "DOGS       (low vol, low margin)    -- review for removal",
}
SEP = "-" * 100


def fmt_inr(v: float) -> str:
    return f"Rs{v:>9,.2f}"


def print_report(rows: list[dict], min_units: float):
    if not rows:
        print("No data — run the importer first:  python -m scripts.import_pos")
        return

    if min_units > 0:
        rows = [r for r in rows if r["units"] >= min_units]
        if not rows:
            print(f"No items with >= {min_units} units sold.")
            return

    # Group by class
    by_class: dict[str, list] = {c: [] for c in CLASS_ORDER}
    for r in rows:
        by_class[r["classification"]].append(r)

    date_note = ""
    total_rev = sum(r["revenue"] for r in rows)
    total_contrib = sum(r["total_contribution"] for r in rows)

    print()
    print("=" * 100)
    print(f"  URS MAJESTIC — Menu Engineering Report")
    print(f"  {len(rows)} food items analysed  |  "
          f"Total revenue: Rs{total_rev:,.2f}  |  "
          f"Total contribution: Rs{total_contrib:,.2f}")
    print("=" * 100)

    hdr = f"  {'ITEM':<40} {'CAT':<22} {'UNITS':>6}  {'AVG PRICE':>10}  {'COST%':>6}  {'CPU':>10}  {'TOTAL CONTRIB':>14}"
    for cls in CLASS_ORDER:
        items = by_class[cls]
        if not items:
            continue
        print()
        print(f"  *** {CLASS_LABEL[cls]} ({len(items)} items) ***")
        print(SEP)
        print(hdr)
        print(SEP)
        for r in items:
            print(
                f"  {r['item']:<40} "
                f"{r['category']:<22} "
                f"{r['units']:>6.0f}  "
                f"{fmt_inr(r['avg_price'])}  "
                f"{r['food_cost_pct']*100:>5.1f}%  "
                f"{fmt_inr(r['contribution_per_unit'])}  "
                f"{fmt_inr(r['total_contribution'])}"
            )

    # Summary table
    print()
    print("=" * 100)
    print(f"  {'CLASS':<12} {'ITEMS':>5}  {'UNITS':>7}  {'REVENUE':>13}  {'CONTRIBUTION':>14}  {'AVG CPU':>10}")
    print(SEP)
    for cls in CLASS_ORDER:
        items = by_class[cls]
        if not items:
            continue
        c_units  = sum(r["units"] for r in items)
        c_rev    = sum(r["revenue"] for r in items)
        c_contrib= sum(r["total_contribution"] for r in items)
        c_cpu    = sum(r["contribution_per_unit"] for r in items) / len(items)
        print(
            f"  {cls:<12} {len(items):>5}  {c_units:>7.0f}  "
            f"{fmt_inr(c_rev)}  {fmt_inr(c_contrib)}  {fmt_inr(c_cpu)}"
        )
    print(SEP)
    all_units = sum(r["units"] for r in rows)
    print(
        f"  {'TOTAL':<12} {len(rows):>5}  {all_units:>7.0f}  "
        f"{fmt_inr(total_rev)}  {fmt_inr(total_contrib)}"
    )
    print("=" * 100)
    print()


def main():
    parser = argparse.ArgumentParser(description="Menu engineering report")
    parser.add_argument(
        "--min-units", type=float, default=0,
        help="Exclude items with fewer than N units sold (default: 0 = show all)"
    )
    parser.add_argument(
        "--class", dest="cls", choices=["Star", "Workhorse", "Puzzle", "Dog"],
        metavar="CLASS",
        help="Show only one class: Star | Workhorse | Puzzle | Dog"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = get_analysis(db)
    finally:
        db.close()

    if args.cls:
        rows = [r for r in rows if r["classification"] == args.cls]

    print_report(rows, args.min_units)


if __name__ == "__main__":
    main()
