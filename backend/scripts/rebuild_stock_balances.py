"""One-off: rebuild every ingredient's stock estimate after the purchase
trigger outage (trg_sync_purchase_to_stock was disabled ~22 Sep - 9 Oct 2026,
so ~130 purchases never reached stock while sales kept being deducted).

For each active ingredient:
  start   = its latest physical count, or -- if it has not been counted since
            the outage began -- the last stock row before the outage
  balance = start
          + menu purchases after the start (dated after the count's evening,
            or entered after the outage snapshot)
          - walk-in usage (petpooja_usage ledger) applied after the start
          - delivery usage (Zomato/Swiggy orders) for dates after the start,
            from DELIVERY_STOCK_FROM on; their ledgers are seeded so later
            uploads only apply differences
One SYSTEM-REBUILD row per ingredient records the result.

    python -m scripts.rebuild_stock_balances            # dry run, prints the changes
    python -m scripts.rebuild_stock_balances --apply    # writes the rows
"""
import argparse
import datetime
import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.clock import business_tz  # noqa: E402
from app.services.sales_stock import (  # noqa: E402
    CHANNEL_LEDGER, DELIVERY_STOCK_FROM, calculate_ingredient_usage, channel_sales,
)
from app.services.stock_count import NON_STOCK_CATEGORIES, NON_STOCK_NAMES, count_day, source_of  # noqa: E402

IST = business_tz()
# The outage: last purchase that reached stock 21 Sep 22:49, first one that
# didn't 23 Sep 11:17. Stock rows up to this moment were still correct.
OUTAGE_SNAPSHOT = datetime.datetime(2026, 9, 23, 11, 0, tzinfo=IST)
NOTE = "SYSTEM-REBUILD 2026-10-09: purchase trigger outage repair"

CONV = {("kg", "g"): Decimal(1000), ("g", "kg"): Decimal("0.001"), ("l", "ml"): Decimal(1000), ("ml", "l"): Decimal("0.001")}


def convert(qty, frm, to, pack_g=None):
    qty = Decimal(str(qty))
    if frm == to or frm is None:
        return qty
    if to == "pcs" and pack_g and frm in ("kg", "g"):
        grams = qty * 1000 if frm == "kg" else qty
        return grams / Decimal(str(pack_g))
    return qty * CONV.get((frm, to), Decimal(1))


def main(apply: bool) -> None:
    db = SessionLocal()
    try:
        items = db.execute(text("""
            SELECT i.id, i.name, i.category, coalesce(v.unit::text, i.unit::text) AS unit, i.pack_size_g
            FROM ingredients i LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
            WHERE i.is_active ORDER BY i.name
        """)).mappings().all()
        # Gas cylinders, uniforms, equipment: not shelf stock, leave them alone.
        items = [i for i in items if i["category"] not in NON_STOCK_CATEGORIES and i["name"] not in NON_STOCK_NAMES]
        rows = defaultdict(list)
        for r in db.execute(text("""
            SELECT id, ingredient_id, on_hand_qty, unit::text AS unit, note, counted_at
            FROM ingredient_stock ORDER BY counted_at, id
        """)).mappings():
            rows[r["ingredient_id"]].append(r)
        purchases = defaultdict(list)
        for p in db.execute(text("""
            SELECT ingredient_id, qty, unit::text AS unit, purchase_date, created_at FROM purchases
            WHERE deleted_at IS NULL AND usage_type = 'menu'
        """)).mappings():
            purchases[p["ingredient_id"]].append(p)

        latest_data = db.execute(text("SELECT max(business_date) FROM daily_channel_sales")).scalar()
        delivery_dates = [DELIVERY_STOCK_FROM + datetime.timedelta(days=n)
                          for n in range((latest_data - DELIVERY_STOCK_FROM).days + 1)]
        delivery = {}  # channel -> {(date, ingredient): (qty, unit)}
        for channel in ("zomato", "swiggy"):
            usage, _ = calculate_ingredient_usage(db, channel_sales(db, channel, delivery_dates))
            delivery[channel] = usage

        changes = []
        for it in items:
            hist = rows.get(it["id"], [])
            humans = [r for r in hist if source_of(r["note"]) == "counted"]
            last_h = humans[-1] if humans else None
            current = hist[-1] if hist else None
            if last_h and last_h["counted_at"] > OUTAGE_SNAPSHOT:
                mode, start_at, start_qty = "count", last_h["counted_at"], convert(last_h["on_hand_qty"], last_h["unit"], it["unit"], it["pack_size_g"])
                c_day = count_day(start_at)
                buys = [p for p in purchases[it["id"]] if p["purchase_date"] > c_day]
                usage_after = lambda d: d > c_day  # noqa: E731
            else:
                before = [r for r in hist if r["counted_at"] <= OUTAGE_SNAPSHOT]
                snap = before[-1] if before else None
                mode, start_at = "snapshot", OUTAGE_SNAPSHOT
                start_qty = convert(snap["on_hand_qty"], snap["unit"], it["unit"], it["pack_size_g"]) if snap else Decimal(0)
                c_day = None
                buys = [p for p in purchases[it["id"]] if p["created_at"] > OUTAGE_SNAPSHOT]
                usage_after = None

            bought = sum((convert(p["qty"], p["unit"], it["unit"], it["pack_size_g"]) for p in buys), Decimal(0))

            # Walk-in usage applied after the start, from the petpooja ledger.
            led_now, led_at_start = {}, {}
            for r in hist:
                if not (r["note"] or "").startswith("petpooja_usage:"):
                    continue
                parts = r["note"].split(":")[:4]
                if len(parts) < 4:
                    continue
                d, used = datetime.date.fromisoformat(parts[1]), convert(parts[2], parts[3], it["unit"], it["pack_size_g"])
                led_now[d] = used
                if r["counted_at"] <= start_at:
                    led_at_start[d] = used
            if mode == "count":
                walkin = sum((u for d, u in led_now.items() if usage_after(d)), Decimal(0))
            else:
                walkin = sum((u - led_at_start.get(d, Decimal(0)) for d, u in led_now.items()), Decimal(0))

            deliv = Decimal(0)
            seed = []
            for channel, usage in delivery.items():
                for (d, ing), (q, unit) in usage.items():
                    if ing != it["id"]:
                        continue
                    seed.append((channel, d, q, unit))
                    if mode == "snapshot" or d > c_day:
                        deliv += convert(q, unit, it["unit"], it["pack_size_g"])

            new = max(Decimal(0), start_qty + bought - walkin - deliv)
            old = convert(current["on_hand_qty"], current["unit"], it["unit"], it["pack_size_g"]) if current else None
            changes.append({**it, "mode": mode, "start": start_qty, "bought": bought, "walkin": walkin,
                            "delivery": deliv, "old": old, "new": new, "seed": seed, "n_buys": len(buys)})

        # An item that never had a stock row and still rebuilds to 0 gets no row:
        # writing one would only add an "estimate at zero" flag.
        moved = [c for c in changes if (c["old"] is None and c["new"] > 0)
                 or (c["old"] is not None and abs(c["new"] - c["old"]) > Decimal("0.0005"))]
        print(f"{len(changes)} active items, {len(moved)} change. Latest sales data {latest_data}.")
        print(f"{'item':<34}{'mode':<9}{'start':>9}{'+bought':>9}{'-walkin':>9}{'-deliv':>9}{'old':>9}{'new':>9} unit")
        for c in sorted(moved, key=lambda c: c["name"]):
            f = lambda x: "" if x is None else f"{float(x):.2f}"  # noqa: E731
            print(f"{c['name'][:33]:<34}{c['mode']:<9}{f(c['start']):>9}{f(c['bought']):>9}{f(c['walkin']):>9}"
                  f"{f(c['delivery']):>9}{f(c['old']):>9}{f(c['new']):>9} {c['unit']}")
        was_zero = sum(1 for c in changes if c["old"] == 0)
        now_zero = sum(1 for c in changes if c["new"] == 0 and c["old"] is not None)
        print(f"\nAt zero: {was_zero} before -> {now_zero} after")

        if not apply:
            print("\nDry run. Re-run with --apply to write.")
            return
        for c in changes:
            # Seed the delivery ledgers (balance unchanged) so later uploads
            # only apply differences, then write the rebuilt balance.
            cur = c["old"] if c["old"] is not None else Decimal(0)
            for channel, d, q, unit in sorted(c["seed"], key=lambda s: (s[0], s[1])):
                db.execute(text("""
                    INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
                    VALUES (:i, :q, CAST(:u AS unit_type), NULL, :n)
                """), {"i": c["id"], "q": cur, "u": c["unit"],
                       "n": f"{CHANNEL_LEDGER[channel]}:{d.isoformat()}:{q.normalize()}:{unit}"})
            if c in moved:
                db.execute(text("""
                    INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
                    VALUES (:i, :q, CAST(:u AS unit_type), NULL, :n)
                """), {"i": c["id"], "q": c["new"], "u": c["unit"],
                       "n": f"{NOTE} (from {c['mode']}; +{c['n_buys']} purchases)"})
        db.commit()
        print(f"\nApplied: {len(moved)} balances rebuilt, delivery ledgers seeded.")
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    main(ap.parse_args().apply)
