"""Menu Analysis: which dishes earn the money, which only fill tables, and
what to change (design/mockups/14-menu-analysis.html).

Dine-in sales (Petpooja item_sales) for the last 7/30/90 days, ending on the
last uploaded day, against the engine-derived food cost per dish. Groups use
the classic menu-engineering split at the menu median of plates sold and
contribution per plate. Petpooja names that don't match a menu item are
listed so they can be linked once (pos_aliases) instead of silently missing.
"""
import datetime
import statistics

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.uploads.item_matching import MenuIndex

PERIODS = (7, 30, 90)
GOAL_FC = 35          # food-cost goal shown on the page, %
HIGH_FC = 45          # above this a popular dish gets a price/cost nudge
RISING = 1.8          # a Star selling this many times its previous period is "taking off"
GROUPS = {
    "S": ("Stars", "Sell more & protect the price"),
    "W": ("Workhorses", "Popular, thin margin: nudge price or trim cost"),
    "P": ("Puzzles", "Good margin, few orders: promote or reposition"),
    "D": ("Dogs", "Few orders, low margin: rework or drop"),
}


def _asof(db: Session) -> datetime.date | None:
    return db.execute(text("SELECT max(sale_date) FROM item_sales")).scalar()


def _sales(db: Session, start: datetime.date, end: datetime.date) -> dict[str, dict]:
    rows = db.execute(text("""
        SELECT item_name, sum(qty)::float AS q, sum(revenue)::float AS rv
          FROM item_sales WHERE sale_date BETWEEN :s AND :e GROUP BY 1
    """), {"s": start, "e": end}).mappings().all()
    return {r["item_name"]: {"q": r["q"], "rv": r["rv"]} for r in rows}


def _step(price: float) -> int:
    """A price nudge of about 7%, in ₹5 steps."""
    return max(5, round(price * 0.07 / 5) * 5)


def page(db: Session, days: int) -> dict:
    days = days if days in PERIODS else 30
    asof = _asof(db)
    if asof is None:
        return {"days": days, "asof": None, "items": []}
    start = asof - datetime.timedelta(days=days - 1)
    prev_end = start - datetime.timedelta(days=1)
    cur = _sales(db, start, asof)
    prev = _sales(db, prev_end - datetime.timedelta(days=days - 1), prev_end)

    menu = db.execute(text("""
        SELECT id, name, category, is_food, is_active, derived_food_cost_pct::float AS fcp, cost_confidence::text AS conf
          FROM menu_items
    """)).mappings().all()
    by_name = {m["name"]: m for m in menu}

    items, uncosted = [], []
    for name, s in cur.items():
        m = by_name.get(name)
        if not m or not m["is_food"] or not m["is_active"] or s["q"] <= 0:
            continue
        if m["fcp"] is None:
            uncosted.append({"n": name, "rv": s["rv"]})
            continue
        ap = s["rv"] / s["q"]
        f = m["fcp"] * 100
        items.append({
            "id": m["id"], "n": name, "c": m["category"], "q": round(s["q"], 2), "rv": round(s["rv"]),
            "ap": round(ap), "f": round(f, 1), "cpu": round(ap * (1 - m["fcp"])),
            "tc": round(s["rv"] * (1 - m["fcp"])), "pq": round(prev.get(name, {}).get("q", 0), 2),
            "prov": m["conf"] != "reliable",
        })

    if items:
        mu = statistics.median(i["q"] for i in items)
        mc = statistics.median(i["cpu"] for i in items)
        for i in items:
            i["k"] = ("S" if i["cpu"] >= mc else "W") if i["q"] >= mu else ("P" if i["cpu"] >= mc else "D")
    else:
        mu = mc = 0
    items.sort(key=lambda i: -i["tc"])

    rev = sum(i["rv"] for i in items)
    tc = sum(i["tc"] for i in items)
    groups = []
    for k, (label, action) in GROUPS.items():
        g = [i for i in items if i["k"] == k]
        groups.append({"k": k, "label": label, "action": action, "n": len(g), "tc": sum(i["tc"] for i in g),
                       "share": (sum(i["tc"] for i in g) / tc * 100) if tc else 0})

    # ── Unlinked Petpooja names, with a best guess from the channel matcher.
    index = MenuIndex(db)
    names = {m["id"]: m["name"] for m in menu}
    unlinked = []
    for name, s in sorted(cur.items(), key=lambda kv: -kv[1]["rv"]):
        if name in by_name:
            continue
        guess = index.match(name)
        unlinked.append({"n": name, "q": round(s["q"], 2), "rv": round(s["rv"]), "guess": guess if guess in names else None})
    unsold = sorted(m["name"] for m in menu if m["is_food"] and m["is_active"] and m["name"] not in cur)
    options = sorted(({"id": m["id"], "name": m["name"], "food": m["is_food"]} for m in menu if m["is_active"]),
                     key=lambda o: o["name"])

    top2 = items[:2]
    return {
        "days": days, "asof": asof, "start": start, "items": items, "groups": groups,
        "median_q": mu, "median_cpu": mc,
        "rev": rev, "rev_all": rev + sum(u["rv"] for u in unlinked) + sum(u["rv"] for u in uncosted),
        "plates": sum(i["q"] for i in items), "tc": tc,
        "fc": (sum(i["f"] * i["rv"] for i in items) / rev) if rev else None,
        "top2_share": (sum(i["tc"] for i in top2) / tc * 100) if tc else 0, "top2": top2,
        "actions": actions(items, days), "unlinked": unlinked, "unlinked_rv": sum(u["rv"] for u in unlinked),
        "uncosted": uncosted, "unsold": unsold, "options": options, "goal_fc": GOAL_FC,
    }


def actions(items: list[dict], days: int) -> list[dict]:
    """Do-this-first list: price/cost nudges, slow costly dishes, then risers."""
    minq = max(3, round(20 * days / 30))
    out = []
    nudge = sorted((i for i in items if i["f"] > HIGH_FC and i["q"] >= minq), key=lambda i: -i["q"])
    for i in nudge[:3]:
        st = _step(i["ap"])
        out.append({"kind": "bad", "title": f"{i['n']}: {i['f']:g}% food cost",
                    "text": f"{i['q']:g} plates at ₹{i['ap']}. ₹{st} more per plate (or ₹{st} less food in it) brings it to {i['f'] * i['ap'] / (i['ap'] + st):.0f}%.",
                    "amt": i["q"] * st, "unit": f"at +₹{st}"})
    if len(nudge) > 3:
        rest = nudge[3:]
        out.append({"kind": "bad", "title": f"{len(rest)} more popular dish{'es' if len(rest) > 1 else ''} over {HIGH_FC}%",
                    "text": " · ".join(f"{i['n']} {i['f']:g}%" for i in rest),
                    "amt": sum(i["q"] * _step(i["ap"]) for i in rest), "unit": "at +5–7%"})
    slow = [i for i in items if i.get("k") == "D" and i["f"] > HIGH_FC]
    if slow:
        out.append({"kind": "warn", "title": f"{len(slow)} slow dish{'es' if len(slow) > 1 else ''} cost over {HIGH_FC}%",
                    "text": ", ".join(i["n"] for i in slow) + ". Few orders and thin margin: rework the recipe or take them off.",
                    "amt": sum(i["rv"] for i in slow), "unit": "sales"})
    for i in sorted((i for i in items if i.get("k") == "S" and i["pq"] > 0 and i["q"] / i["pq"] >= RISING),
                    key=lambda i: -i["tc"])[:2]:
        out.append({"kind": "good", "title": f"{i['n']} is taking off",
                    "text": f"{i['pq']:g} → {i['q']:g} plates vs the {days} days before, at {i['f']:g}% food cost. Keep it on the counter board and suggest it.",
                    "amt": i["tc"], "unit": "contribution"})
    return out


def link(db: Session, pos_name: str, menu_item_id: int) -> int:
    """Link a Petpooja name to a menu item: saved for future uploads and
    applied to past sales. Returns the number of sale rows relabelled."""
    name = db.execute(text("SELECT name FROM menu_items WHERE id = :i"), {"i": menu_item_id}).scalar()
    if name is None:
        raise ValueError("Unknown menu item")
    db.execute(text("""
        INSERT INTO pos_aliases (menu_item_id, pos_name) VALUES (:i, :n)
        ON CONFLICT (pos_name) DO UPDATE SET menu_item_id = EXCLUDED.menu_item_id
    """), {"i": menu_item_id, "n": pos_name})
    n = db.execute(text("""
        UPDATE item_sales SET item_name = :m WHERE item_name = :n OR raw_name = :n
    """), {"m": name, "n": pos_name}).rowcount
    db.commit()
    return n
