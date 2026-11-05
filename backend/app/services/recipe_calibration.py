"""Suggest recipe-gram corrections from the gap between what stock counts say was
really used and what the recipes deducted.

Between two real counts of an item: really used = opening count + bought or moved
in - moved out - closing count; recipes say = the sales deductions in that stretch.
One stretch is noisy (deliveries land mid-stretch, counts are rounded), so a
suggestion needs several stretches that add up and lean the same way. Nothing is
changed here without the owner pressing Apply, which scales every dish line of
the item by the ratio.
"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.web.audit import log_change, resync_derived_costs

LOOKBACK_DAYS = 45
MIN_STRETCHES = 5
OFF_BY = (0.8, 1.25)      # inside this the recipes are close enough
SANE = (0.5, 2.5)         # outside this grams are not the problem: a missing mapping or batch
LEAN = 0.6                # share of stretches that must lean the same way
BATCH = "recipe_calibration"

_USAGE = ("petpooja_usage", "zomato_usage", "swiggy_usage", "pace_usage")


def kind_of(note: str | None) -> str:
    """Classify a ledger row by its note: use / in / out / sys / count."""
    n = note or ""
    if n.startswith(_USAGE):
        return "use"
    if n.startswith(("purchase_auto", "transfer_in")):
        return "in"
    if n.startswith("transfer_out"):
        return "out"
    if n.startswith(("SYSTEM", "gravy_batch", "unit_fix")):
        return "sys"
    return "count"


def stretches(seq: list[tuple], since=None) -> list[dict]:
    """seq: [(balance, at, kind)] oldest first. One stretch per pair of consecutive
    real counts; a stretch touched by a system or batch row is dropped, and so is one
    opened before `since` (the last time this item was calibrated)."""
    out = []
    anchor = None
    used = inn = outt = 0.0
    dirty = False
    prev = None
    for q, at, k in seq:
        if k == "count":
            if anchor is not None and not dirty and (since is None or anchor[1] >= since):
                out.append({"from": anchor[1], "to": at, "recipe": used, "real": anchor[0] + inn - outt - q})
            anchor, used, inn, outt, dirty = (q, at), 0.0, 0.0, 0.0, False
        elif anchor is not None:
            d = prev - q
            if k == "use":
                used += d
            elif k == "in":
                inn -= d
            elif k == "out":
                outt += d
            else:
                dirty = True
        prev = q
    return out


def decide(wins: list[dict]) -> dict | None:
    """Turn an item's stretches into a verdict, or None when there is no case to answer.
    verdict 'scale' has a ratio in the sane range; 'check' means the gap is too big to be
    a grams error (usually the dish list or a gravy batch is missing something)."""
    if len(wins) < MIN_STRETCHES:
        return None
    recipe = sum(w["recipe"] for w in wins)
    real = sum(w["real"] for w in wins)
    if recipe <= 0 or real <= 0:
        return None
    ratio = real / recipe
    if OFF_BY[0] < ratio < OFF_BY[1]:
        return None
    up = sum(1 for w in wins if w["recipe"] > 0 and w["real"] > w["recipe"])
    dn = sum(1 for w in wins if w["recipe"] > 0 and w["real"] < w["recipe"])
    if max(up, dn) / max(up + dn, 1) < LEAN:
        return None
    return {"verdict": "scale" if SANE[0] <= ratio <= SANE[1] else "check", "ratio": ratio,
            "stretches": len(wins), "recipe": recipe, "real": real}


def _ledger(db: Session) -> dict:
    rows = db.execute(text("""
        SELECT s.ingredient_id, i.name, s.unit::text AS unit, s.on_hand_qty::float AS q, s.counted_at, s.note
          FROM ingredient_stock s JOIN ingredients i ON i.id = s.ingredient_id
         WHERE i.is_active AND s.counted_at >= now() - make_interval(days => :d)
         ORDER BY s.ingredient_id, s.counted_at, s.id"""), {"d": LOOKBACK_DAYS}).all()
    seqs: dict = defaultdict(list)
    for r in rows:
        seqs[(r.ingredient_id, r.name, r.unit)].append((r.q, r.counted_at, kind_of(r.note)))
    return seqs


def _last_calibrated(db: Session) -> dict:
    return {r[0]: r[1] for r in db.execute(text(
        "SELECT target_id, max(repaired_at) FROM cost_base_repair_log WHERE batch = :b GROUP BY target_id"), {"b": BATCH})}


def _dish_lines(db: Session, ingredient_id: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT m.id, m.menu_item_id, mi.name, coalesce(m.grams_override, m.portion_override_g)::float AS grams
          FROM ingredient_dish_map m JOIN menu_items mi ON mi.id = m.menu_item_id
         WHERE m.ingredient_id = :i AND mi.is_active AND NOT coalesce(m.stock_from_batch, false)
           AND coalesce(m.grams_override, m.portion_override_g) > 0
         ORDER BY mi.name"""), {"i": ingredient_id}).mappings().all()
    return [dict(r) for r in rows]


def suggestions(db: Session) -> dict:
    """{'scale': [...], 'check': [...]} for the Stock Log."""
    last = _last_calibrated(db)
    scale, check = [], []
    for (iid, name, unit), seq in _ledger(db).items():
        v = decide(stretches(seq, last.get(iid)))
        if not v:
            continue
        row = {"id": iid, "name": name, "unit": unit, **v}
        if v["verdict"] == "scale":
            ratio = round(v["ratio"], 2)
            row["ratio"] = ratio
            row["dishes"] = [{**d, "new": round(min(d["grams"] * ratio, 5000), 2)} for d in _dish_lines(db, iid)]
            if not row["dishes"]:
                continue
            scale.append(row)
        else:
            check.append(row)
    scale.sort(key=lambda r: -abs(r["ratio"] - 1))
    check.sort(key=lambda r: -abs(r["ratio"] - 1))
    return {"scale": scale, "check": check}


def apply(db: Session, ingredient_id: int, user_id: int) -> dict:
    """Scale every dish line of the item by its current suggested ratio. Recomputed here,
    so a stale page cannot apply a ratio the data no longer supports."""
    found = next((s for s in suggestions(db)["scale"] if s["id"] == ingredient_id), None)
    if not found:
        raise ValueError("That item is no longer suggested; reload the page")
    ratio = found["ratio"]
    reason = f"Calibrated from {found['stretches']} counts: really used {found['real']:.3g} vs recipes {found['recipe']:.3g} {found['unit']} (x{ratio})"
    for d in found["dishes"]:
        db.execute(text("""UPDATE ingredient_dish_map SET grams_override = :g, portion_override_g = NULL, grams_source = 'calibrated'
                            WHERE id = :id"""), {"g": Decimal(str(d["new"])), "id": d["id"]})
        log_change(db, batch=BATCH, target_table="ingredient_dish_map", target_id=d["id"], field="grams_override",
                   old_value=Decimal(str(round(d["grams"], 2))), new_value=Decimal(str(d["new"])), reason=reason, actor_user_id=user_id)
    # The marker row that restarts evidence for this item: only later counts count.
    log_change(db, batch=BATCH, target_table="ingredients", target_id=ingredient_id, field="ratio",
               old_value=None, new_value=Decimal(str(ratio)), reason=reason, actor_user_id=user_id)
    resync_derived_costs(db)
    db.commit()
    return {"ratio": ratio, "dishes": len(found["dishes"])}
