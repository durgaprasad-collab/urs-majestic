"""Is a staff request valid? (design/mockups/16-request-check.html)

For a requested item: the last purchase, how many plates its dishes have
covered since, what the recipes say those plates used, and the stock figure
-- turned into a verdict the owner reads on Buy before approving:

  ok   Valid               the last purchase is used up, or about a day left
  ok   Valid · order ahead  a few days left; worth ordering if it's delivered
  chk  Check                recipes say a good share of it should be left
  no   Doubtful             none of its dishes have sold since it was bought
  na   Can't check          no dish uses it (cleaning, extras, ...)
  na   Staff food           bought for staff meals (category 'Staff')

Recipe grams are still estimates for most dishes, so this advises; it never
blocks a request.
"""
import datetime
import re
import statistics

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.services.purchases_page import to_item_unit

ORDER_AHEAD_DAYS = 4
LOW_DAYS = 1.5


def _words(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", s.lower()) if len(w) > 1}


# Kitchen names staff type (Hindi / Tamil) -> the item's name. Fresh ginger
# is logged under Ginger-Garlic by house convention. Garlic is left out on
# purpose: it goes to Chinese Garlic, Ginger-Garlic or Garlic case by case.
LOCAL_NAMES = {
    "adrak": "Ginger-Garlic", "inji": "Ginger-Garlic", "ginger": "Ginger-Garlic",
    "dhanya": "Coriander", "dhaniya": "Coriander", "dhania": "Coriander", "kothamalli": "Coriander", "kothamali": "Coriander",
    "pyaz": "Onion", "pyaaz": "Onion", "kanda": "Onion", "vengayam": "Onion",
    "tamatar": "Tomato", "thakkali": "Tomato",
    "aloo": "Potato", "alu": "Potato", "urulai": "Potato",
    "mirchi": "Green Chilli", "hari mirch": "Green Chilli", "milagai": "Green Chilli", "pachai milagai": "Green Chilli",
    "pudina": "Mint", "gobi": "Gobi", "gobhi": "Gobi", "phool gobi": "Gobi",
    "shimla mirch": "Capsicum", "gajar": "Carrot", "patta gobi": "Cabbage", "bandh gobi": "Cabbage",
    "nimbu": "Lemon", "elumichai": "Lemon", "doodh": "Milk", "paal": "Milk", "dahi": "Curd", "thayir": "Curd",
    "makhan": "Butter", "chawal": "Rice/Basmati", "tel": "Oil", "ennai": "Oil", "maida": "Maida", "atta": "Wheat Flour",
}


def match_ingredient(db: Session, name: str) -> int | None:
    """Best item for a typed name: a known kitchen name ("Adrak" -> ginger,
    "Dhanya" -> Coriander), exact (any case), then the same words in any order
    ("Basmati rice" -> "Rice/Basmati"), then the one item whose name holds
    every typed word. None when it's ambiguous or nothing fits."""
    name = (name or "").strip()
    if not name:
        return None
    local = LOCAL_NAMES.get(re.sub(r"\s+", " ", name.lower()))
    if local:
        name = local
    rows = db.execute(text("SELECT id, name FROM ingredients WHERE is_active")).all()
    for iid, n in rows:
        if n.lower() == name.lower():
            return iid
    want = _words(name)
    if not want:
        return None
    same = [iid for iid, n in rows if _words(n) == want]
    if len(same) == 1:
        return same[0]
    holds = [iid for iid, n in rows if want <= _words(n)]
    return holds[0] if len(holds) == 1 else None


def _fmt(q: float | None, unit: str) -> str:
    if q is None:
        return "—"
    if unit == "g" and q >= 1000:
        return f"{q / 1000:.1f} kg"
    if unit == "ml" and q >= 1000:
        return f"{q / 1000:.1f} L"
    shown = f"{q:.2f}" if q < 10 else f"{q:.1f}"
    return f"{float(shown):g} {'L' if unit == 'l' else unit}"


def check(db: Session, ingredient_id: int, before: datetime.datetime | None = None) -> dict | None:
    ing = db.execute(text("SELECT id, name, unit::text AS u, pack_size_g, category FROM ingredients WHERE id = :i"),
                     {"i": ingredient_id}).mappings().first()
    if not ing:
        return None
    unit = ing["u"]
    before = before or datetime.datetime.now(datetime.timezone.utc)
    buys = db.execute(text("""
        SELECT purchase_date AS d, qty, unit::text AS u, total_price, vendor FROM purchases
         WHERE ingredient_id = :i AND deleted_at IS NULL AND created_at < :b
           AND (usage_type = 'menu' OR :staff)
         ORDER BY purchase_date DESC, id DESC LIMIT 12
    """), {"i": ingredient_id, "b": before, "staff": ing["category"] == "Staff"}).mappings().all()
    last = buys[0] if buys else None
    last_qty = to_item_unit(float(last["qty"]), last["u"], unit, ing["pack_size_g"]) if last else None
    days = sorted({b["d"] for b in buys})
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    gap = statistics.median(gaps) if gaps else None

    # Grams per plate are in g (ml for liquids); convert to the item's unit.
    div = 1000.0 if unit in ("kg", "l") else (float(ing["pack_size_g"]) if unit == "pcs" and ing["pack_size_g"] else 1.0)
    today = business_today()
    since = last["d"] if last else today - datetime.timedelta(days=30)
    d30 = today - datetime.timedelta(days=30)
    # Dishes using it directly, plus combos through their parts (Combo 01 =
    # half a Jeera Rice, ...) -- the same way stock deduction explodes combos.
    # A combo's own legacy map rows are ignored, as the cost engine does.
    dishes = db.execute(text("""
        WITH uses AS (
            SELECT mi.id, mi.name, coalesce(m.grams_override, m.portion_override_g) AS g
              FROM ingredient_dish_map m JOIN menu_items mi ON mi.id = m.menu_item_id
             WHERE m.ingredient_id = :i AND mi.is_active
               AND mi.id NOT IN (SELECT combo_menu_item_id FROM combo_components)
            UNION ALL
            SELECT cm.id, cm.name, c.portion_factor * coalesce(m.grams_override, m.portion_override_g)
              FROM combo_components c
              JOIN menu_items cm ON cm.id = c.combo_menu_item_id
              JOIN ingredient_dish_map m ON m.menu_item_id = c.component_menu_item_id
             WHERE m.ingredient_id = :i AND cm.is_active
        ), per AS (SELECT id, name, sum(g) AS g FROM uses GROUP BY id, name)
        SELECT per.id, per.name, per.g,
               coalesce((SELECT sum(qty) FROM item_sales s WHERE s.item_name = per.name AND s.sale_date >= :since), 0)
             + coalesce((SELECT sum(oi.quantity) FROM order_items oi JOIN orders o ON o.id = oi.order_id
                          WHERE oi.menu_item_id = per.id AND o.placed_at::date >= :since), 0) AS plates,
               coalesce((SELECT sum(qty) FROM item_sales s WHERE s.item_name = per.name AND s.sale_date >= :d30), 0)
             + coalesce((SELECT sum(oi.quantity) FROM order_items oi JOIN orders o ON o.id = oi.order_id
                          WHERE oi.menu_item_id = per.id AND o.placed_at::date >= :d30), 0) AS plates30
          FROM per
    """), {"i": ingredient_id, "since": since, "d30": d30}).mappings().all()
    plates = sum(float(d["plates"]) for d in dishes)
    used = sum(float(d["plates"]) * float(d["g"] or 0) for d in dishes) / div
    daily = sum(float(d["plates30"]) * float(d["g"] or 0) for d in dishes) / div / 30
    top = sorted(((d["name"], float(d["plates"]), float(d["g"] or 0)) for d in dishes if d["plates"]),
                 key=lambda x: -x[1] * x[2])[:4]
    stock = db.execute(text("""SELECT on_hand_qty FROM ingredient_stock WHERE ingredient_id = :i AND counted_at < :b
                               ORDER BY counted_at DESC, id DESC LIMIT 1"""), {"i": ingredient_id, "b": before}).scalar()
    stock = float(stock) if stock is not None else None
    count = db.execute(text("""SELECT on_hand_qty, counted_at FROM ingredient_stock
                                WHERE ingredient_id = :i AND counted_at < :b AND counted_by IS NOT NULL
                                ORDER BY counted_at DESC, id DESC LIMIT 1"""), {"i": ingredient_id, "b": before}).first()

    out = {
        "ingredient_id": ingredient_id, "name": ing["name"], "unit": unit, "staff": ing["category"] == "Staff",
        "last": {"d": last["d"], "qty": last_qty, "rs": float(last["total_price"]), "vendor": last["vendor"]} if last else None,
        "gap": gap, "n_dishes": len(dishes), "plates": round(plates), "used": used, "daily": daily,
        "stock": stock, "count": {"qty": float(count[0]), "at": count[1]} if count else None, "top": top,
        "used_pct": (used / last_qty * 100) if last_qty else None,
    }
    out.update(_verdict(out))
    out["fmt"] = {k: _fmt(out[k], unit) for k in ("used", "daily", "stock")}
    out["fmt"]["last_qty"] = _fmt(last_qty, unit)
    if out["count"]:
        out["fmt"]["count"] = _fmt(out["count"]["qty"], unit)
    return out


def _verdict(x: dict) -> dict:
    u, last, name = x["unit"], x["last"], x["name"]
    f = lambda q: _fmt(q, u)  # noqa: E731
    day = lambda d: f"{d.day} {d:%b}"  # noqa: E731
    if x.get("staff"):
        tail = f"Last bought {day(last['d'])} ({f(last['qty'])}, ₹{last['rs']:,.0f})" if last else "Not bought before"
        rhythm = f"; usually every {round(x['gap'])} days." if x["gap"] else "."
        return {"k": "na", "label": "Staff food", "why": f"{name} is bought for staff meals, so dish sales can't check it. {tail}{rhythm}"}
    if not x["n_dishes"]:
        tail = f"Last bought {day(last['d'])} ({f(last['qty'])}, ₹{last['rs']:,.0f})" if last else "Never bought"
        rhythm = f"; usually bought every {round(x['gap'])} days." if x["gap"] else "."
        return {"k": "na", "label": "Can't check", "why": f"No dish uses {name}, so sales can't say whether it's used up. {tail}{rhythm}"}
    if not last:
        return {"k": "ok", "label": "Valid", "why": f"Never bought before, and {x['n_dishes']} dishes use it."}
    q = last["qty"]
    if not x["plates"]:
        n = x["n_dishes"]
        return {"k": "no", "label": "Doubtful",
                "why": f"None of the {n} dish{'es' if n > 1 else ''} using {name} has sold since it was last bought on {day(last['d'])}. The {f(q)} bought then should still be there."}
    left, daily, used, plates = x["stock"], x["daily"], x["used"], x["plates"]
    days_left = (left / daily) if (left is not None and daily) else None
    left_txt = ""
    if left is not None:
        left_txt = f" The stock figure says {f(left)} left"
        if days_left is not None:
            left_txt += f", {'under a day' if days_left < 1 else f'~{days_left:.1f} days'} at {f(daily)}/day"
        left_txt += "."
    if q and used > q * 1.5:
        return {"k": "ok", "label": "Valid", "missing": True,
                "why": f"Recipes used {f(used)} since {day(last['d'])}, {round(used / q)}× the {f(q)} bought then. It's used up, and some purchases were probably never logged (or it's made in-house)."}
    if (q and used >= q * 0.8) or (days_left is not None and days_left <= LOW_DAYS):
        pct = f" ({round(used / q * 100)}%)" if q else ""
        return {"k": "ok", "label": "Valid", "why": f"{f(q)} bought {day(last['d'])} has covered {plates} plates; recipes used {f(used)}{pct}.{left_txt}"}
    if days_left is not None and days_left <= ORDER_AHEAD_DAYS:
        return {"k": "ok", "label": "Valid · order ahead",
                "why": f"Recipes used {f(used)} of the {f(q)} bought {day(last['d'])} ({plates} plates).{left_txt} Worth ordering now if it comes by delivery."}
    should = max((q or 0) - used, 0)
    if left is not None:
        should = left
    return {"k": "chk", "label": "Check", "left_hint": f(should),
            "why": f"Recipes used only {f(used)} of the {f(q)} bought {day(last['d'])} ({plates} plates). About {f(should)} should be left"
                   + (f", ~{days_left:.0f} days" if days_left is not None else "") + ". Ask the kitchen to look before buying."}
