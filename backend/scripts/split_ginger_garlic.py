"""Split "Ginger-Garlic" (id 13) into Ginger and Garlic (made in the kitchen, 50:50).

  python -m scripts.split_ginger_garlic          # dry run: prints what would change
  python -m scripts.split_ginger_garlic --apply  # does it (one transaction, with backups)

What it does
  1. Creates Ginger and Garlic (kg, recipe items). Chinese Garlic (id 65) is untouched.
  2. Every dish that used Ginger-Garlic now uses half as much Ginger and half as much Garlic
     (the paste is 50:50), so cost, stock use and reorder forecasts follow without engine changes.
  3. Old purchases move to Ginger or Garlic when the note says which. Rows that don't say
     (no note, or both in one line) stay on the old item and are listed for you to decide.
  4. Each new item gets an opening stock count of half the last Ginger-Garlic count.
  5. The old item is retired (inactive, renamed) so it leaves the Buy and Stock lists.
Backups: ingredient_dish_map_backup_gg_split (the old map rows).
"""
import re
import sys

from sqlalchemy import text

from app.core.database import SessionLocal

OLD_ID = 13
STRIP = re.compile(r"ginger[\s-]*garlic|gg paste|\bgg\b", re.I)


def kind(note: str | None) -> str | None:
    """'ginger' / 'garlic' when the note names exactly one of them, else None."""
    n = STRIP.sub(" ", note or "")
    g, i = bool(re.search(r"garlic|poondu|lahsun", n, re.I)), bool(re.search(r"ginger|adrak|inji", n, re.I))
    if "garlic for" in (note or "").lower():  # "garlic for GG paste"
        return "garlic"
    if g == i:
        return None
    return "garlic" if g else "ginger"


def run(apply: bool) -> None:
    db = SessionLocal()
    old = db.execute(text("SELECT * FROM ingredients WHERE id = :i"), {"i": OLD_ID}).mappings().one()
    if not old["is_active"] and old["name"].startswith("[SPLIT"):
        print("Already split."); return
    maps = db.execute(text("SELECT * FROM ingredient_dish_map WHERE ingredient_id = :i"), {"i": OLD_ID}).mappings().all()
    buys = db.execute(text("""SELECT id, purchase_date, qty, total_price, notes FROM purchases
                              WHERE ingredient_id = :i AND deleted_at IS NULL ORDER BY purchase_date"""), {"i": OLD_ID}).mappings().all()
    plan = {"ginger": [], "garlic": [], "left": []}
    for b in buys:
        plan[kind(b["notes"]) or "left"].append(b)
    stock = db.execute(text("SELECT on_hand_qty FROM ingredient_stock WHERE ingredient_id = :i ORDER BY counted_at DESC, id DESC LIMIT 1"),
                       {"i": OLD_ID}).scalar()
    print(f"dish rows to split: {len(maps)}")
    print(f"purchases -> Ginger: {len(plan['ginger'])}, -> Garlic: {len(plan['garlic'])}, left for you: {len(plan['left'])}")
    for b in plan["left"]:
        per = float(b["total_price"]) / float(b["qty"]) if b["qty"] else 0
        print(f"   ? #{b['id']} {b['purchase_date']} {float(b['qty']):g} kg Rs{b['total_price']} (Rs{per:.0f}/kg) {(b['notes'] or '')[:60]}")
    print(f"opening stock: {float(stock or 0):.3f} kg -> {float(stock or 0) / 2:.3f} kg each")
    if not apply:
        print("\nDry run. Nothing changed. Re-run with --apply."); return

    ids = {}
    for name in ("Ginger", "Garlic"):
        ids[name] = db.execute(text("""
            INSERT INTO ingredients (name, unit, category, is_active, cost_role, portion_light_g, portion_medium_g,
                                     portion_heavy_g, order_increment_qty)
            VALUES (:n, :u, :c, true, :r, :l / 2, :m / 2, :h / 2, :o) RETURNING id"""),
            {"n": name, "u": old["unit"], "c": old["category"], "r": old["cost_role"], "l": old["portion_light_g"],
             "m": old["portion_medium_g"], "h": old["portion_heavy_g"], "o": old["order_increment_qty"]}).scalar()
    db.execute(text("DROP TABLE IF EXISTS ingredient_dish_map_backup_gg_split"))
    db.execute(text("CREATE TABLE ingredient_dish_map_backup_gg_split AS SELECT * FROM ingredient_dish_map WHERE ingredient_id = :i"), {"i": OLD_ID})
    for m in maps:
        for name in ("Ginger", "Garlic"):
            db.execute(text("""
                INSERT INTO ingredient_dish_map (ingredient_id, menu_item_id, intensity, grams_override, portion_override_g,
                                                 grams_source, stock_from_batch)
                VALUES (:i, :d, :int, :g / 2, :p / 2, :s, :b)"""),
                {"i": ids[name], "d": m["menu_item_id"], "int": m["intensity"], "g": m["grams_override"],
                 "p": m["portion_override_g"], "s": m["grams_source"], "b": m["stock_from_batch"]})
    db.execute(text("DELETE FROM ingredient_dish_map WHERE ingredient_id = :i"), {"i": OLD_ID})
    for name, rows in (("Ginger", plan["ginger"]), ("Garlic", plan["garlic"])):
        for b in rows:
            db.execute(text("""UPDATE purchases SET ingredient_id = :n, row_version = row_version + 1,
                               notes = concat_ws(' | ', notes, 'Moved from Ginger-Garlic when it was split into Ginger and Garlic') WHERE id = :p"""),
                       {"n": ids[name], "p": b["id"]})
    if stock is not None:
        for name in ("Ginger", "Garlic"):
            db.execute(text("""INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, note)
                               VALUES (:i, :q, :u, 'opening: half of last Ginger-Garlic count')"""),
                       {"i": ids[name], "q": float(stock) / 2, "u": old["unit"]})
    db.execute(text("UPDATE ingredients SET is_active = false, name = :n WHERE id = :i"),
               {"n": "[SPLIT into Ginger + Garlic - DO NOT USE] Ginger-Garlic", "i": OLD_ID})
    db.commit()
    print("Done.", ids)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run("--apply" in sys.argv)
