"""Recipes (was Ingredient Mapping): what goes into each dish, in grams, and
what it costs (design/mockups/15-recipes.html).

Every recipe line has its own grams (grams_override): an ingredient's amount
differs in every dish, so there are no shared light/medium/heavy defaults.
grams_source says whether that number was confirmed for the dish ('weighed')
or copied from the old tier by migration 0047 ('estimate'). A line with two
saved weights (grams_override vs legacy portion_override_g) is shown for the
owner to pick one. Costs come from the cost engine's own helpers so the page
and Menu Analysis agree.
"""
import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.menu_engineering import cost_engine as ce
from app.services.purchases_page import to_item_unit

SPICES = ce._SPICE_CATEGORY


def _f(x):
    return float(x) if x is not None else None


def data(db: Session) -> dict:
    cost_g, _ = ce._ingredient_cost_per_g(db)
    spice = ce._spice_per_dish(db)
    pack = ce._dish_packaging_cost(db)
    parcel = ce._parcel_rate(db)
    asof = db.execute(text("SELECT max(sale_date) FROM item_sales")).scalar() or datetime.date.today()
    since = asof - datetime.timedelta(days=29)

    dishes = db.execute(text("""
        SELECT mi.id, mi.name, mi.category, mi.price, mi.derived_food_cost_pct, mi.cost_confidence::text AS conf,
               coalesce(s.q, 0) AS q, coalesce(o.q, 0) AS qd
          FROM menu_items mi
          LEFT JOIN (SELECT item_name, sum(qty) q FROM item_sales WHERE sale_date BETWEEN :s AND :e GROUP BY 1) s ON s.item_name = mi.name
          LEFT JOIN (SELECT oi.menu_item_id, sum(oi.quantity) q FROM order_items oi JOIN orders od ON od.id = oi.order_id
                      WHERE od.placed_at::date BETWEEN :s AND :e AND oi.menu_item_id IS NOT NULL GROUP BY 1) o ON o.menu_item_id = mi.id
         WHERE mi.is_active AND mi.is_food
         ORDER BY coalesce(s.q, 0) + coalesce(o.q, 0) DESC, mi.name
    """), {"s": since, "e": asof}).mappings().all()
    combo_rows = db.execute(text("""
        SELECT c.combo_menu_item_id AS cid, c.component_menu_item_id AS comp_id, m.name, c.portion_factor, c.fixed_cost, c.is_guess
          FROM combo_components c LEFT JOIN menu_items m ON m.id = c.component_menu_item_id ORDER BY c.id
    """)).mappings().all()
    combos: dict[int, list] = {}
    for r in combo_rows:
        combos.setdefault(r["cid"], []).append({"id": r["comp_id"], "n": r["name"], "pf": _f(r["portion_factor"]),
                                                "fc": _f(r["fixed_cost"]), "guess": bool(r["is_guess"])})

    rows = db.execute(text("""
        SELECT m.id, m.menu_item_id, m.ingredient_id, m.intensity::text AS tier, m.grams_override, m.portion_override_g, m.grams_source,
               i.name, i.unit::text AS unit, i.category, i.cost_role::text AS role,
               i.portion_light_g, i.portion_medium_g, i.portion_heavy_g
          FROM ingredient_dish_map m JOIN ingredients i ON i.id = m.ingredient_id
          JOIN menu_items mi ON mi.id = m.menu_item_id
         WHERE mi.is_active AND mi.is_food
    """)).mappings().all()
    lines: dict[int, list] = {}
    for r in rows:
        go, po = r["grams_override"], r["portion_override_g"]
        g = go if go is not None else (po if po is not None else r[f"portion_{r['tier']}_g"])
        pg = cost_g.get(r["ingredient_id"])
        lines.setdefault(r["menu_item_id"], []).append({
            "id": r["id"], "iid": r["ingredient_id"], "i": r["name"], "u": "ml" if r["unit"] in ("l", "ml") else "g",
            "cat": r["category"], "role": r["role"], "g": _f(g),
            "src": "weighed" if r["grams_source"] == "weighed" else "estimate",
            "conflict": go is not None and po is not None and go != po, "go": _f(go), "po": _f(po),
            "kg": round(float(pg) * 1000, 2) if pg is not None else None,
        })

    bought: dict[int, float] = {}
    for r in db.execute(text("""
        SELECT p.ingredient_id, p.qty, p.unit::text AS unit, i.unit::text AS item_unit, i.pack_size_g
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.usage_type = 'menu' AND p.purchase_date BETWEEN :s AND :e
    """), {"s": since, "e": asof}).mappings():
        q = to_item_unit(float(r["qty"]), r["unit"], r["item_unit"], r["pack_size_g"])
        if q is not None:
            bought[r["ingredient_id"]] = bought.get(r["ingredient_id"], 0) + q

    ings = db.execute(text("""
        SELECT i.id, i.name, i.unit::text AS unit, i.category, i.cost_role::text AS role,
               i.pack_size_g,
               (SELECT count(*) FROM ingredient_dish_map x JOIN menu_items mi ON mi.id = x.menu_item_id
                 WHERE x.ingredient_id = i.id AND mi.is_active AND mi.is_food) AS nd
          FROM ingredients i WHERE i.is_active ORDER BY i.name
    """)).mappings().all()

    return {
        "asof": asof.isoformat(), "since": since.isoformat(), "spice": float(spice), "parcel": float(parcel),
        "dishes": [{"id": d["id"], "n": d["name"], "c": d["category"], "p": float(d["price"]),
                    "f": round(float(d["derived_food_cost_pct"]) * 100, 1) if d["derived_food_cost_pct"] is not None else None,
                    "conf": d["conf"], "q": float(d["q"]), "qd": float(d["qd"]), "combo": d["id"] in combos,
                    "pack": round(float(pack.get(d["id"], 0)), 2)} for d in dishes],
        "lines": lines, "combos": combos,
        "ings": [{"id": i["id"], "name": i["name"], "u": i["unit"], "c": i["category"], "role": i["role"],
                  "pack_g": _f(i["pack_size_g"]), "nd": int(i["nd"]),
                  "kg": round(float(cost_g[i["id"]]) * 1000, 2) if i["id"] in cost_g else None,
                  "bought": round(bought.get(i["id"], 0), 3)} for i in ings],
    }


def _num(v, lo=0, hi=5000) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except Exception:
        return None
    return d if lo <= d <= hi else None


def save_dish(db: Session, menu_item_id: int, changes: list[dict], adds: list[dict], removes: list[int]) -> None:
    """Grams edits, new ingredients and removals for one dish."""
    for c in changes:
        g = _num(c.get("grams"))
        if g is None:
            raise ValueError("Grams must be a number between 0 and 5000")
        db.execute(text("""UPDATE ingredient_dish_map SET grams_override = :g, portion_override_g = NULL, grams_source = 'weighed'
                            WHERE id = :id AND menu_item_id = :m"""), {"g": g, "id": int(c["id"]), "m": menu_item_id})
    for a in adds:
        g = _num(a.get("grams"))
        if g is None:
            raise ValueError("Grams must be a number between 0 and 5000")
        exists = db.execute(text("SELECT id FROM ingredient_dish_map WHERE menu_item_id = :m AND ingredient_id = :i"),
                            {"m": menu_item_id, "i": int(a["ingredient_id"])}).scalar()
        if exists:
            db.execute(text("UPDATE ingredient_dish_map SET grams_override = :g, portion_override_g = NULL, grams_source = 'weighed' WHERE id = :id"),
                       {"g": g, "id": exists})
        else:
            db.execute(text("""INSERT INTO ingredient_dish_map (ingredient_id, menu_item_id, intensity, grams_override, grams_source)
                               VALUES (:i, :m, 'medium', :g, 'weighed')"""), {"i": int(a["ingredient_id"]), "m": menu_item_id, "g": g})
    for rid in removes:
        db.execute(text("DELETE FROM ingredient_dish_map WHERE id = :id AND menu_item_id = :m"), {"id": int(rid), "m": menu_item_id})
    _recost(db)


def confirm_dish(db: Session, menu_item_id: int) -> int:
    """Mark every estimated line of a dish as confirmed, as is."""
    n = db.execute(text("""UPDATE ingredient_dish_map SET grams_source = 'weighed'
                            WHERE menu_item_id = :m AND grams_source = 'estimate' AND grams_override IS NOT NULL"""),
                   {"m": menu_item_id}).rowcount
    db.commit()
    return n


def set_role(db: Session, ingredient_id: int, role: str) -> None:
    if role not in ("recipe", "overhead", "per_order"):
        raise ValueError("Unknown role")
    db.execute(text("UPDATE ingredients SET cost_role = CAST(:r AS cost_role_type) WHERE id = :id"), {"r": role, "id": ingredient_id})
    _recost(db)


def _recost(db: Session) -> None:
    """Recompute dish costs so Menu Analysis reflects the change, then commit."""
    db.flush()
    ce.run_cost_engine(db)
    db.commit()
