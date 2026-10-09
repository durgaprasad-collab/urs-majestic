"""Nightly stock count: what to count, what is a real count vs an estimate,
and owner-only item disabling. Shared by the admin Stock Log page, the staff
app's Count tab and the 10 PM reminder.

Stock rows come from three places, and the UI must never confuse them:
  counted   a person entered it (stock log, staff app, admin correction)
  sales     petpooja_usage:* / zomato_usage:* / swiggy_usage:* - recipes x
            dishes sold (walk-in and delivery), deducted automatically
  purchase  purchase_auto:*  - added automatically when a purchase is logged
  system    SYSTEM-COMPUTED rows from one-off repairs
"""
import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_now, business_tz
from app.web.audit import log_change

# Not things you count on a shelf.
NON_STOCK_CATEGORIES = {"Apparel", "Equipment", "Overhead", "Staff"}
NON_STOCK_NAMES = {"Cooking Gas"}
# Counted every night; everything stockable is counted on FULL_COUNT_WEEKDAY.
DAILY_CATEGORIES = {"Vegetables", "Dairy", "Frozen"}
FULL_COUNT_WEEKDAY = 0  # Monday
# A count entered before this hour belongs to the previous evening's count
# (the kitchen closes at 1 AM, so a 10 PM count can run past midnight).
COUNT_DAY_ROLLOVER_HOUR = 5
LOW_COVER_DAYS = 3
STALE_COUNT_DAYS = 7
FIRST_MAX = 20

CATEGORY_ORDER = [
    "Vegetables", "Dairy", "Frozen", "Dry Goods", "Spices", "Condiments", "Pulses", "Flour",
    "Utilities", "packaging", "Beverage - Resale",
]
_AUTO = ("petpooja_usage", "zomato_usage", "swiggy_usage", "purchase_auto", "SYSTEM")
# SQL filter for "a person entered this row" (keep in step with _AUTO).
HUMAN_ROW_SQL = ("coalesce(s.note, '') NOT LIKE 'petpooja_usage%' AND coalesce(s.note, '') NOT LIKE 'zomato_usage%' "
                 "AND coalesce(s.note, '') NOT LIKE 'swiggy_usage%' AND coalesce(s.note, '') NOT LIKE 'purchase_auto%' "
                 "AND coalesce(s.note, '') NOT LIKE 'SYSTEM%'")


def source_of(note: str | None) -> str:
    note = note or ""
    if note.startswith(("petpooja_usage", "zomato_usage", "swiggy_usage")):
        return "sales"
    if note.startswith("purchase_auto"):
        return "purchase"
    if note.startswith("SYSTEM"):
        return "system"
    return "counted"


def count_day(at: datetime.datetime | None = None) -> datetime.date:
    """The business evening a count belongs to (00:00-04:59 rolls back a day)."""
    at = (at.astimezone(business_tz()) if at else business_now())
    if at.hour < COUNT_DAY_ROLLOVER_HOUR:
        at -= datetime.timedelta(days=1)
    return at.date()


def is_full_count_day(day: datetime.date) -> bool:
    return day.weekday() == FULL_COUNT_WEEKDAY


def _human_counts(db: Session) -> dict[int, dict]:
    rows = db.execute(text(f"""
        SELECT DISTINCT ON (s.ingredient_id) s.ingredient_id, s.on_hand_qty, s.counted_at, u.name AS by_name
        FROM ingredient_stock s LEFT JOIN users u ON u.id = s.counted_by
        WHERE {HUMAN_ROW_SQL}
        ORDER BY s.ingredient_id, s.counted_at DESC
    """)).mappings().all()
    return {r["ingredient_id"]: dict(r) for r in rows}


def stock_view(db: Session) -> list[dict]:
    """Every active ingredient with its estimate, last real count and flags."""
    from app.web.stock_log_routes import _stock_rows  # avoid a circular import at load

    tonight = count_day()
    human = _human_counts(db)
    items = []
    for r in _stock_rows(db):
        h = human.get(r["ingredient_id"])
        cat = r["category"] or "Other"
        cover = r["cover_days"]
        qty = float(r["on_hand_qty"]) if r["on_hand_qty"] is not None else None
        src = source_of(r["note"]) if qty is not None else None
        h_day = count_day(h["counted_at"]) if h else None
        item = {
            "id": r["ingredient_id"], "name": r["name"], "category": cat, "unit": r["unit"],
            "pack": bool(r["pack_size_g"]),
            "qty": qty, "source": src, "estimated": src not in (None, "counted"),
            "cover": cover, "stale": bool(r["stock_stale"]),
            "last_qty": float(h["on_hand_qty"]) if h else None,
            "last_at": h["counted_at"].astimezone(business_tz()) if h else None,
            "last_by": h["by_name"] if h else None,
            "last_days": (tonight - h_day).days if h_day else None,
            "counted_tonight": h_day == tonight,
            "stockable": cat not in NON_STOCK_CATEGORIES and r["name"] not in NON_STOCK_NAMES,
        }
        item["f_low"] = cover is not None and cover < LOW_COVER_DAYS
        item["f_zero"] = item["estimated"] and qty == 0
        item["f_recount"] = item["stale"]
        item["f_never"] = h is None
        item["f_old"] = item["last_days"] is not None and item["last_days"] > STALE_COUNT_DAYS
        item["why"] = [w for w, on in (
            ("estimate at zero", item["f_zero"]),
            ("low cover", item["f_low"] and not item["f_zero"]),
            ("purchase since last count", item["f_recount"]),
            ("never counted", item["f_never"]),
            (f"not counted for {item['last_days']} days", item["f_old"]),
        ) if on]
        # Counted tonight = done: it leaves "count these first" even if it's
        # still low (low cover is a Buy problem, not a counting one).
        item["first"] = (item["stockable"] and not item["counted_tonight"]
                         and (item["f_zero"] or item["f_low"] or item["f_recount"]))
        item["rank"] = (0 if item["f_zero"] else 1 if item["f_low"] else 2) * 1000 + (cover if cover is not None else 99) * 10
        items.append(item)
    return items


def category_sort_key(cat: str) -> tuple:
    return (CATEGORY_ORDER.index(cat) if cat in CATEGORY_ORDER else len(CATEGORY_ORDER), cat)


def tonights_list(items: list[dict], day: datetime.date | None = None) -> tuple[str, list[dict]]:
    """('full' | 'daily', items to count). Daily = perishables + anything running
    low or bought since its last count; the full list on the full-count day."""
    day = day or count_day()
    stock = [i for i in items if i["stockable"]]
    if is_full_count_day(day):
        chosen = stock
        kind = "full"
    else:
        chosen = [i for i in stock if i["category"] in DAILY_CATEGORIES or i["f_low"] or i["f_recount"]]
        kind = "daily"
    chosen = sorted(chosen, key=lambda i: (category_sort_key(i["category"]), i["name"].lower()))
    return kind, chosen


def summary(db: Session, items: list[dict]) -> dict:
    """Header numbers: tonight's progress, last real count, flags."""
    day = count_day()
    kind, todo = tonights_list(items, day)
    stock = [i for i in items if i["stockable"]]
    last = db.execute(text(f"""
        SELECT (s.counted_at AT TIME ZONE 'Asia/Kolkata')::date AS d, count(DISTINCT s.ingredient_id) n,
               mode() WITHIN GROUP (ORDER BY u.name) AS by_name
        FROM ingredient_stock s LEFT JOIN users u ON u.id = s.counted_by
        WHERE {HUMAN_ROW_SQL}
        GROUP BY 1 ORDER BY 1 DESC LIMIT 1
    """)).mappings().first()
    return {
        "day": day, "kind": kind,
        "todo_total": len(todo), "todo_done": sum(1 for i in todo if i["counted_tonight"]),
        "stock_total": len(stock), "counted_tonight": sum(1 for i in stock if i["counted_tonight"]),
        "last_day": last["d"] if last else None, "last_n": last["n"] if last else 0,
        "last_by": last["by_name"] if last else None,
        "low": sum(1 for i in stock if i["f_low"]), "zero": sum(1 for i in stock if i["f_zero"]),
        "recount": sum(1 for i in stock if i["f_recount"]), "never": sum(1 for i in stock if i["f_never"]),
        "old": sum(1 for i in stock if i["f_old"]),
        "first_total": sum(1 for i in stock if i["first"]),
    }


def first_list(items: list[dict]) -> list[dict]:
    return sorted([i for i in items if i["first"]], key=lambda i: i["rank"])[:FIRST_MAX]


# ── Owner-only: disable / re-enable an item ──────────────────────────────────

def set_active(db: Session, ingredient_id: int, active: bool, reason: str, user_id: int) -> str | None:
    """Flip ingredients.is_active with an audit row. Returns the item name, or
    None if nothing changed. A disabled item drops out of every list that
    filters on is_active (stock log, count, forecast, requests). Commits."""
    row = db.execute(text("SELECT name, is_active FROM ingredients WHERE id = :i FOR UPDATE"),
                     {"i": ingredient_id}).mappings().first()
    if row is None or row["is_active"] == active:
        return None
    log_change(db, batch="ingredient_active", target_table="ingredients", target_id=ingredient_id,
               field="is_active", old_value=row["is_active"], new_value=active,
               reason=reason or ("Re-enabled" if active else "Disabled"), actor_user_id=user_id)
    db.execute(text("UPDATE ingredients SET is_active = :a WHERE id = :i"), {"a": active, "i": ingredient_id})
    db.commit()
    return row["name"]


def disabled_items(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT i.id, i.name, i.category,
               (SELECT l.reason FROM cost_base_repair_log l
                 WHERE l.target_table = 'ingredients' AND l.target_id = i.id AND l.field = 'is_active'
                 ORDER BY l.repaired_at DESC LIMIT 1) AS reason
        FROM ingredients i WHERE NOT i.is_active ORDER BY i.name
    """)).mappings().all()]
