"""Stock reconciliation: for every ingredient, the best estimate of what is on the shelf
(last physical count + everything bought since - what recipes say sales used) set
against what the Stock Log screen currently shows. Live version of the one-off
reconciliation sheet; read-only.

Status, worst first:
  never  no physical count exists
  neg    the estimate is below zero (missing purchase bills, or portions set too high)
  stale  the last count is over STALE_DAYS old
  diff   the Stock Log differs from the estimate by more than 20% and two days of use
  ok
"""
import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.services import stock_count

STALE_DAYS = 30
RANK = {"never": 0, "neg": 1, "stale": 2, "diff": 3, "ok": 4}
LABEL = {"never": "Never counted", "neg": "Below zero", "stale": "Count too old",
         "diff": "Screens disagree", "ok": "OK"}


def _bought_since(db: Session, since_by_item: dict[int, datetime.date]) -> dict[int, float]:
    from app.services.purchases_page import to_item_unit
    if not since_by_item:
        return {}
    lo = min(since_by_item.values())
    rows = db.execute(text("""
        SELECT p.ingredient_id, p.qty, p.unit::text AS unit, p.purchase_date, i.unit::text AS item_unit, i.pack_size_g
          FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
         WHERE p.deleted_at IS NULL AND p.purchase_date > :lo
    """), {"lo": lo}).mappings().all()
    out: dict[int, float] = {}
    for r in rows:
        since = since_by_item.get(r["ingredient_id"])
        if since is None or r["purchase_date"] <= since:
            continue
        q = to_item_unit(float(r["qty"]), r["unit"], r["item_unit"], r["pack_size_g"])
        if q is not None:
            out[r["ingredient_id"]] = out.get(r["ingredient_id"], 0.0) + q
    return out


def _used_since(db: Session, since_by_item: dict[int, datetime.date]) -> dict[int, float]:
    """Recipe usage the sales ledger recorded after each item's count day, in the stock unit
    (the latest note per channel/day/item is the applied figure)."""
    if not since_by_item:
        return {}
    lo = min(since_by_item.values())
    rows = db.execute(text("""
        SELECT DISTINCT ON (ingredient_id, ch, d) ingredient_id, d, q FROM (
            SELECT ingredient_id, split_part(note, '_usage:', 1) AS ch, split_part(note, ':', 2) AS d,
                   split_part(note, ':', 3) AS q, counted_at, id
              FROM ingredient_stock
             WHERE note ~ '^(petpooja|zomato|swiggy|pace)_usage:' AND split_part(note, ':', 2) > :lo
        ) x ORDER BY ingredient_id, ch, d, counted_at DESC, id DESC
    """), {"lo": lo.isoformat()}).mappings().all()
    out: dict[int, float] = {}
    for r in rows:
        since = since_by_item.get(r["ingredient_id"])
        if since is None or r["d"] <= since.isoformat():
            continue
        try:
            out[r["ingredient_id"]] = out.get(r["ingredient_id"], 0.0) + float(r["q"])
        except ValueError:
            continue
    return out


def _note(r: dict) -> str:
    st, u = r["status"], r["unit"]
    if st == "never":
        return "No physical count exists. Weigh it once so every screen has a starting point."
    if st == "neg":
        return "Recipes use more than was counted plus bought: a purchase bill is missing, or the portion is set too high."
    if st == "stale":
        return f"Last count is {r['age']} days old; drift has built up. Weigh it."
    if st == "diff":
        gap = (r["slog"] or 0) - r["est"]
        return f"Stock Log is {abs(gap):,.2f} {u} {'higher' if gap > 0 else 'lower'} than the estimate; weigh to settle."
    if r["cover"] is not None and r["cover"] < 1:
        return "Matches, but under a day of cover: reorder."
    return "Matches."


def reconcile(db: Session) -> dict:
    today = business_today()
    items = [i for i in stock_count.stock_view(db) if i["stockable"]]
    # The count day each estimate starts from (a count before 5 AM belongs to the evening before).
    since = {i["id"]: stock_count.count_day(i["last_at"]) for i in items if i["last_at"] and i["last_qty"] is not None}
    bought = _bought_since(db, since)
    used = _used_since(db, since)
    rows = []
    for i in items:
        iid, k = i["id"], i.get("k") or 1.0
        est = None
        if iid in since:
            est = i["last_qty"] + bought.get(iid, 0.0) - used.get(iid, 0.0) * k
        age = (today - since[iid]).days if iid in since else None
        per_day = i["use"]["daily"] if i["use"] else None
        slog = i["qty"]
        status = "ok"
        if est is None:
            status = "never"
        elif est < 0:
            status = "neg"
        elif age is not None and age > STALE_DAYS:
            status = "stale"
        elif slog is not None and abs(slog - est) > max(0.2 * abs(est), 2 * (per_day or 0)) and abs(slog - est) > 0.0001:
            status = "diff"
        cover = (est / per_day) if (est is not None and per_day) else None
        r = {"id": iid, "name": i["name"], "category": i["category"], "unit": i["unit"], "status": status,
             "count_day": since.get(iid), "count_qty": i["last_qty"], "age": age,
             "bought": bought.get(iid, 0.0) if iid in since else None,
             "used": used.get(iid, 0.0) * k if iid in since else None,
             "est": est, "slog": slog, "home": i["home"], "per_day": per_day, "cover": cover}
        r["note"] = _note(r)
        rows.append(r)
    rows.sort(key=lambda r: (RANK[r["status"]], r["name"].lower()))
    return {"day": today, "rows": rows, "label": LABEL,
            "counts": {k: sum(1 for r in rows if r["status"] == k) for k in LABEL}}
