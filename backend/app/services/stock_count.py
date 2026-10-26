"""Nightly stock count: what to count, what is a real count vs an estimate,
and owner-only item disabling. Shared by the admin Stock Log page, the staff
app's Count tab and the 10 PM reminder.

Stock rows come from three places, and the UI must never confuse them:
  counted   a person entered it (stock log, staff app, admin correction)
  sales     petpooja_usage:* / zomato_usage:* / swiggy_usage:* - recipes x
            dishes sold (walk-in and delivery), deducted automatically
  pace      pace_usage:*     - items sales can't deduct, taken off daily at
            their buying pace
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
_AUTO = ("petpooja_usage", "zomato_usage", "swiggy_usage", "pace_usage", "purchase_auto", "SYSTEM")
# SQL filter for "a person entered this row" (keep in step with _AUTO).
HUMAN_ROW_SQL = ("coalesce(s.note, '') NOT LIKE 'petpooja_usage%' AND coalesce(s.note, '') NOT LIKE 'zomato_usage%' "
                 "AND coalesce(s.note, '') NOT LIKE 'swiggy_usage%' AND coalesce(s.note, '') NOT LIKE 'pace_usage%' "
                 "AND coalesce(s.note, '') NOT LIKE 'purchase_auto%' "
                 "AND coalesce(s.note, '') NOT LIKE 'SYSTEM%'")


def source_of(note: str | None) -> str:
    note = note or ""
    if note.startswith(("petpooja_usage", "zomato_usage", "swiggy_usage")):
        return "sales"
    if note.startswith("pace_usage"):
        return "pace"
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
        SELECT DISTINCT ON (s.ingredient_id) s.ingredient_id, s.on_hand_qty, s.unit::text AS unit, s.counted_at, u.name AS by_name
        FROM ingredient_stock s LEFT JOIN users u ON u.id = s.counted_by
        WHERE {HUMAN_ROW_SQL}
        ORDER BY s.ingredient_id, s.counted_at DESC
    """)).mappings().all()
    return {r["ingredient_id"]: dict(r) for r in rows}


UNIT_FACTOR = {("kg", "g"): 1000.0, ("g", "kg"): 0.001, ("l", "ml"): 1000.0, ("ml", "l"): 0.001}


def _in_item_unit(h: dict, r: dict, per_kg: float | None) -> float:
    """A count row's qty in the item's unit (the row may predate a unit change)."""
    q = float(h["on_hand_qty"])
    if r["unit"] == "pcs" and r["pack_size_g"]:
        return round(q * per_kg, 2) if h["unit"] != "pcs" else q
    if h["unit"] == r["unit"]:
        return q
    return round(q * UNIT_FACTOR.get((h["unit"], r["unit"]), 1.0), 2)


def stock_view(db: Session) -> list[dict]:
    """Every active ingredient with its estimate, last real count and flags."""
    from app.web.stock_log_routes import _stock_rows  # avoid a circular import at load

    from app.services.buy_inbox import _resale_daily  # sales-based use for resale drinks

    tonight = count_day()
    human = _human_counts(db)
    resale = _resale_daily(db)
    ingest = _item_ingestion(db)
    by_sales = sales_use(db)
    pace = {r[0]: (datetime.date.fromisoformat(r[1]), r[2].astimezone(business_tz())) for r in db.execute(text("""
        SELECT ingredient_id, max(split_part(note, ':', 2)), max(counted_at) FROM ingredient_stock
         WHERE note LIKE 'pace_usage:%' GROUP BY 1
    """))}
    items = []
    for r in _stock_rows(db):
        h = human.get(r["ingredient_id"])
        cat = r["category"] or "Other"
        cover = r["cover_days"]
        if resale.get(r["ingredient_id"]) and r["on_hand_qty"] is not None:
            cover = float(r["on_hand_qty"]) / resale[r["ingredient_id"]]
        qty = float(r["on_hand_qty"]) if r["on_hand_qty"] is not None else None
        # Pack-sized piece items are stocked in kg; show them in pieces (bottles,
        # bunches), the unit staff count in. record_stock converts back.
        per_kg = (1000.0 / float(r["pack_size_g"])) if (r["unit"] == "pcs" and r["pack_size_g"]) else None
        # Stock is kept in the forecast unit, which can differ from the item's
        # own unit (Briyani essence: item in g, stock in kg). Show it in the
        # item's unit -- the unit edits are typed in (record_stock converts back).
        if not per_kg:
            per_kg = UNIT_FACTOR.get((r["stock_unit"], r["unit"]))
        if per_kg and qty is not None:
            qty = round(qty * per_kg, 2)
        # Uses / day: by sales for recipe items (and resale drinks), else by
        # buying pace (purchases / days between bills) from the forecast view.
        k = per_kg or 1.0
        sales = by_sales.get(r["ingredient_id"])
        buy_daily = float(r["daily_consumption"]) if r["daily_consumption"] else None
        use = None
        if sales:
            use = {"kind": "sales", "daily": sales["daily"] * k, "peak": sales["peak"] * k, "peak_day": sales["peak_day"]}
            if r["on_hand_qty"] is not None and not resale.get(r["ingredient_id"]):
                cover = float(r["on_hand_qty"]) / sales["daily"]
        elif resale.get(r["ingredient_id"]):
            use = {"kind": "sales", "daily": resale[r["ingredient_id"]] * k}
        elif buy_daily:
            use = {"kind": "buying", "daily": buy_daily * k}
        if use and buy_daily:
            use["every"] = float(r["avg_interval_days"]) if r.get("avg_interval_days") else None
            if use["kind"] == "sales":
                diff = (buy_daily * k - use["daily"]) / use["daily"]
                if abs(diff) > USE_GAP:
                    use["gap"] = {"buy": buy_daily * k, "pct": round(abs(diff) * 100), "lower": diff < 0}
        src = source_of(r["note"]) if qty is not None else None
        h_day = count_day(h["counted_at"]) if h else None
        item = {
            "id": r["ingredient_id"], "name": r["name"], "category": cat, "unit": r["unit"],
            "pack": bool(r["pack_size_g"]),
            "qty": qty, "source": src, "estimated": src not in (None, "counted"),
            "cover": cover, "stale": bool(r["stock_stale"]), "use": use,
            "last_qty": _in_item_unit(h, r, per_kg) if h else None,
            "ingest": ingest.get(r["ingredient_id"]), "pace_upto": pace.get(r["ingredient_id"]),
            "last_at": h["counted_at"].astimezone(business_tz()) if h else None,
            "last_by": h["by_name"] if h else None,
            # Last update of any kind (count, sales, purchase, batch) -- not just a real count.
            "upd_at": r["counted_at"], "upd_by": r.get("updated_by"),
            "last_days": (tonight - h_day).days if h_day else None,
            "counted_tonight": h_day == tonight,
            "stockable": cat not in NON_STOCK_CATEGORIES and r["name"] not in NON_STOCK_NAMES,
        }
        # Packaging isn't taken off stock by sales, so between counts its
        # "days left" only grows with purchases. Without a recent count, say so.
        if cat in ("Utilities", "packaging") and src not in ("counted", "pace") and (item["last_days"] is None or item["last_days"] > STALE_COUNT_DAYS):
            cover = item["cover"] = None
            item["pack_uncounted"] = True
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


INGEST_LABEL = {"petpooja": "Petpooja", "zomato": "Zomato", "swiggy": "Swiggy"}


def last_ingestion(db: Session) -> list[dict]:
    """When sales were last taken off stock, per channel: the latest sales day
    deducted and when that upload ran (from the usage rows themselves)."""
    rows = db.execute(text("""
        SELECT split_part(note, '_usage:', 1) AS ch, max(split_part(split_part(note, ':', 2), ':', 1)) AS upto,
               max(counted_at) AS ran
          FROM ingredient_stock WHERE note ~ '^(petpooja|zomato|swiggy)_usage:'
         GROUP BY 1
    """)).mappings().all()
    yesterday = business_now().date() - datetime.timedelta(days=1)
    out = []
    for ch in ("petpooja", "zomato", "swiggy"):
        r = next((x for x in rows if x["ch"] == ch), None)
        if not r:
            continue
        upto = datetime.date.fromisoformat(r["upto"])
        out.append({"label": INGEST_LABEL[ch], "upto": upto, "ran": r["ran"].astimezone(business_tz()),
                    "behind": (yesterday - upto).days})
    return out


def _item_ingestion(db: Session) -> dict[int, list[dict]]:
    """{ingredient_id: [per channel {label, upto, ran, last_sold}]}.

    upto/ran: the channel's latest sales day taken off stock and when (from
    last_ingestion) -- every item's stock reflects that channel up to then.
    last_sold: the item's own latest deduction from that channel, shown when
    it is older (the item didn't sell there since)."""
    chans = {g["label"]: g for g in last_ingestion(db)}
    rows = db.execute(text("""
        SELECT DISTINCT ON (ingredient_id, ch) ingredient_id, ch, upto FROM (
            SELECT ingredient_id, split_part(note, '_usage:', 1) AS ch, split_part(note, ':', 2) AS upto, counted_at, id
              FROM ingredient_stock WHERE note ~ '^(petpooja|zomato|swiggy)_usage:'
        ) x ORDER BY ingredient_id, ch, upto DESC, counted_at DESC, id DESC
    """)).mappings().all()
    mine: dict[int, dict] = {}
    for r in rows:
        try:
            mine.setdefault(r["ingredient_id"], {})[INGEST_LABEL.get(r["ch"], r["ch"])] = datetime.date.fromisoformat(r["upto"])
        except ValueError:
            continue
    out = {}
    for iid, last in mine.items():
        out[iid] = [{"label": lab, "upto": g["upto"], "ran": g["ran"],
                     "last_sold": last.get(lab) if last.get(lab) and last.get(lab) < g["upto"] else None,
                     "never": lab not in last}
                    for lab, g in chans.items()]
    return out


USE_DAYS = 7          # sales days averaged for "uses / day"
USE_GAP = 0.25        # sales vs buying pace differ by more than this -> say so


def sales_use(db: Session) -> dict[int, dict]:
    """{ingredient_id: {daily, peak, peak_day}} in the stock unit, from what
    sales took off stock (recipe grams x dishes sold) over the last USE_DAYS
    Petpooja sales days, all channels. The latest ledger note per
    channel/day/item is the applied figure (re-runs overwrite)."""
    rows = db.execute(text("""
        SELECT DISTINCT ON (ingredient_id, ch, d) ingredient_id, d, q FROM (
            SELECT ingredient_id, split_part(note, '_usage:', 1) AS ch, split_part(note, ':', 2) AS d,
                   split_part(note, ':', 3) AS q, counted_at, id
              FROM ingredient_stock
             WHERE note ~ '^(petpooja|zomato|swiggy)_usage:' AND counted_at > now() - interval '30 days'
        ) x ORDER BY ingredient_id, ch, d, counted_at DESC, id DESC
    """)).mappings().all()
    days = sorted({r["d"] for r in rows})[-USE_DAYS:]
    if not days:
        return {}
    per: dict[int, dict[str, float]] = {}
    for r in rows:
        if r["d"] in days:
            try:
                q = float(r["q"])
            except ValueError:
                continue
            per.setdefault(r["ingredient_id"], {}).setdefault(r["d"], 0.0)
            per[r["ingredient_id"]][r["d"]] += q
    out = {}
    for iid, by_day in per.items():
        total = sum(by_day.values())
        if total <= 0:
            continue
        peak_d = max(by_day, key=by_day.get)
        out[iid] = {"daily": total / len(days), "peak": by_day[peak_d],
                    "peak_day": datetime.date.fromisoformat(peak_d)}
    return out
