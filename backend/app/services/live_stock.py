"""Live stock: today's walk-in sales so far (Petpooja's live Item Report, read by
the Chrome helper) turned into ingredient use and shown against stock.

A view only. Nothing here touches ingredient_stock: tonight's normal upload
takes the day off stock for real, and a count entered today wins (only the
sales after it come off). Petpooja's item report covers walk-in / counter
sales only; Zomato and Swiggy join after their own uploads.
"""
import datetime
import hashlib
import json
import math
import os
import secrets
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_now, business_tz
from app.core.config import settings
from app.services.sales_stock import Sale, calculate_ingredient_usage, share_after
from app.services.stock_count import HUMAN_ROW_SQL, NON_STOCK_CATEGORIES, NON_STOCK_NAMES, UNIT_FACTOR, count_day

STALE_MIN = 30           # a read older than this is shown as stale, not live
MIN_PROJECT_SHARE = 0.25  # before this share of the day has passed, don't project the rest of it
MAX_ROWS = 500
KEEP_DAYS = 4
_SEED = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "menu_seed.json"))


# ── helper codes ──────────────────────────────────────────────────────────────

def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_token(db: Session, user_id: int, label: str = "Chrome helper") -> str:
    """A new helper code. Shown once; only its hash is kept. Caller commits."""
    code = "urslf_" + secrets.token_urlsafe(24)
    db.execute(text("INSERT INTO live_feed_tokens (token_hash, label, created_by_user_id) VALUES (:h, :l, :u)"),
               {"h": _hash(code), "l": (label or "Chrome helper")[:60], "u": user_id})
    return code


def check_token(db: Session, bearer: str | None) -> dict | None:
    """The token row for a 'Bearer urslf_...' header, or None. Marks it used."""
    code = (bearer or "").removeprefix("Bearer ").strip()
    if not code.startswith("urslf_"):
        return None
    row = db.execute(text("SELECT id, label FROM live_feed_tokens WHERE token_hash = :h AND revoked_at IS NULL"),
                     {"h": _hash(code)}).mappings().first()
    if row:
        db.execute(text("UPDATE live_feed_tokens SET last_used_at = now() WHERE id = :i"), {"i": row["id"]})
    return dict(row) if row else None


def tokens(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT id, label, created_at, last_used_at FROM live_feed_tokens WHERE revoked_at IS NULL ORDER BY id DESC"""))
        .mappings()]


# ── storing a read ────────────────────────────────────────────────────────────

def clean_rows(raw) -> list[dict]:
    """[{name, qty, amount}] from what the helper sent; raises ValueError."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("no rows")
    if len(raw) > MAX_ROWS:
        raise ValueError("too many rows")
    out = []
    for r in raw:
        if isinstance(r, (list, tuple)):  # [name, date, qty, total] as the report table gives it
            name, qty, amount = r[0], r[-2], r[-1]
        else:
            name, qty, amount = r.get("name"), r.get("qty"), r.get("amount")
        name = str(name or "").strip()
        qty, amount = float(str(qty).replace(",", "")), float(str(amount or 0).replace(",", ""))
        if not name or not math.isfinite(qty) or not math.isfinite(amount) or qty < 0 or len(name) > 300:
            raise ValueError(f"bad row: {name[:40]!r}")
        row = {"name": name, "qty": qty, "amount": amount}
        if isinstance(r, (list, tuple)) and len(r) >= 4 and str(r[1])[:4].isdigit():
            row["date"] = str(r[1])[:10]  # the report's date column: the business day these sales belong to
        out.append(row)
    return out


def save_read(db: Session, token_id: int | None, rows_raw, client_read_at: str | None = None) -> dict:
    rows = clean_rows(rows_raw)
    day = count_day(business_now())
    dates = {r["date"] for r in rows if r.get("date")}
    if dates and dates != {day.isoformat()}:  # e.g. the report rolled over to a new date
        raise ValueError(f"The report is for {', '.join(sorted(dates))}, not today's business day {day.isoformat()}")
    dishes = sum(r["qty"] for r in rows)
    amount = sum(r["amount"] for r in rows)
    client_at = None
    if client_read_at:
        try:
            client_at = datetime.datetime.fromisoformat(client_read_at.replace("Z", "+00:00"))
        except ValueError:
            client_at = None
    db.execute(text("""
        INSERT INTO live_sales_reads (business_date, client_read_at, rows, dishes, amount, token_id)
        VALUES (:d, :c, CAST(:r AS jsonb), :n, :a, :t)"""),
        {"d": day, "c": client_at, "r": json.dumps(rows), "n": dishes, "a": amount, "t": token_id})
    db.execute(text("DELETE FROM live_sales_reads WHERE business_date < :d"), {"d": day - datetime.timedelta(days=KEEP_DAYS)})
    return {"business_date": day.isoformat(), "rows": len(rows), "dishes": dishes, "amount": amount}


# ── the live view ─────────────────────────────────────────────────────────────

def _resolver(db: Session):
    from app.models.menu_item import MenuItem
    from scripts.import_pos import build_resolver, pos_alias_map
    with open(_SEED, encoding="utf-8") as f:
        seed = json.load(f)
    menu = {m.name: m for m in db.query(MenuItem).all()}
    return build_resolver(seed, menu, pos_alias_map(db)), menu


def _fmt_dt(dt: datetime.datetime | None) -> str | None:
    return dt.astimezone(business_tz()).isoformat() if dt else None


def classify(now_bal: float, close: float | None, full_day: float | None) -> tuple[str, str]:
    """(status, label) for an item: out now / out before close / under a day at close / ok."""
    if now_bal <= 0:
        return "out", "Out now"
    if close is not None and close < 0:
        return "out", "Out before close"
    if close is not None and full_day and close < full_day:
        return "tight", "Under a day at close"
    return "ok", "OK"


def snapshot(db: Session) -> dict:
    now = business_now()
    today = count_day(now)
    reads = db.execute(text("""
        SELECT id, read_at, client_read_at, rows, dishes, amount FROM live_sales_reads
         WHERE business_date = :d ORDER BY read_at, id"""), {"d": today}).mappings().all()
    base = {"today": today.isoformat(), "now": _fmt_dt(now), "has_helper": bool(tokens(db))}
    if not reads:
        return {**base, "status": "none"}
    last = reads[-1]
    age_min = max(0.0, (now - last["read_at"]).total_seconds() / 60)
    status = "live" if age_min <= STALE_MIN else "stale"
    rows = list(last["rows"])
    resolver, menu = _resolver(db)

    sales, unmapped, dish_list, dishes = [], [], [], 0.0
    for r in rows:
        canon = resolver.get(r["name"])
        item = menu.get(canon) if canon else None
        if item is None:
            unmapped.append({"name": r["name"], "qty": r["qty"]})
            continue
        sales.append(Sale(canon, r["qty"], today))
        if getattr(item, "is_food", True):
            dishes += r["qty"]
            dish_list.append({"name": canon, "qty": r["qty"]})  # Parcel / Water Bottle aren't dishes
    dish_list.sort(key=lambda d: -d["qty"])
    merged: dict[str, float] = {}
    for d in dish_list:
        merged[d["name"]] = merged.get(d["name"], 0) + d["qty"]
    top = [{"name": n, "qty": q} for n, q in sorted(merged.items(), key=lambda kv: -kv[1])[:8]]

    # dishes by hour, from the gaps between reads
    hours: dict[int, float] = {}
    prev = float(reads[0]["dishes"])  # the day's first read has no earlier read to compare with
    for rd in reads[1:]:
        h = rd["read_at"].astimezone(business_tz()).hour
        h = h if h >= 5 else h + 24
        hours[h] = hours.get(h, 0.0) + max(float(rd["dishes"]) - prev, 0.0)
        prev = float(rd["dishes"])

    # How long the totals have stood still: Petpooja syncs its orders to the cloud in batches and can
    # run well behind the till, so "read just now" doesn't mean "sales up to now".
    still_since = last["read_at"]
    for rd in reversed(reads[:-1]):
        if float(rd["dishes"]) == float(last["dishes"]) and float(rd["amount"]) == float(last["amount"]):
            still_since = rd["read_at"]
        else:
            break
    still_min = max(0.0, (last["read_at"] - still_since).total_seconds() / 60)

    out = {**base, "status": status, "read_at": _fmt_dt(last["read_at"]), "age_min": round(age_min, 1),
           "still_since": _fmt_dt(still_since), "still_min": round(still_min, 1),
           "reads_today": len(reads), "dishes": dishes, "amount": float(last["amount"]), "top": top,
           "unmapped": unmapped, "hours": [{"h": h, "n": round(n, 1)} for h, n in sorted(hours.items())],
           "rows": [], "notice": None, "tight": 0, "out": 0}

    ingested = db.execute(text("SELECT 1 FROM ingredient_stock WHERE note LIKE :p LIMIT 1"),
                          {"p": f"petpooja_usage:{today.isoformat()}:%"}).first()
    if ingested:
        return {**out, "status": "ingested",
                "notice": "Today's sales have already been taken off stock, so the live view is paused to avoid counting them twice."}
    if not sales:
        return out

    usage, _ = calculate_ingredient_usage(db, sales)
    ids = sorted({iid for (_, iid) in usage})
    if not ids:
        return out
    stock = {r["ingredient_id"]: r for r in db.execute(text("""
        SELECT DISTINCT ON (s.ingredient_id) s.ingredient_id, s.on_hand_qty, s.unit::text AS stock_unit,
               i.name, i.unit::text AS item_unit, i.pack_size_g, i.category, i.is_active
          FROM ingredient_stock s JOIN ingredients i ON i.id = s.ingredient_id
         WHERE s.ingredient_id = ANY(:ids) ORDER BY s.ingredient_id, s.counted_at DESC, s.id DESC"""), {"ids": ids}).mappings()}
    counts = {r["ingredient_id"]: r for r in db.execute(text(f"""
        SELECT DISTINCT ON (s.ingredient_id) s.ingredient_id, s.counted_at,
               (timezone(:tz, s.counted_at) - interval '5 hours')::date AS count_day
          FROM ingredient_stock s WHERE s.ingredient_id = ANY(:ids) AND {HUMAN_ROW_SQL}
         ORDER BY s.ingredient_id, s.counted_at DESC"""), {"ids": ids, "tz": settings.BUSINESS_TIMEZONE}).mappings()}

    read_at = last["read_at"]
    a_now = float(share_after(db, "petpooja_usage", today, read_at))  # share of the day still to come
    f_t = max(1 - a_now, 0.0)                                         # share already behind us
    a_cache: dict[datetime.datetime, float] = {}

    rows_out = []
    for (_, iid), (qty, unit) in usage.items():
        st = stock.get(iid)
        if not st or not st["is_active"] or (st["category"] or "") in NON_STOCK_CATEGORIES or st["name"] in NON_STOCK_NAMES:
            continue
        if st["stock_unit"] != unit:
            continue  # balance in another unit than the usage: nothing sensible to subtract
        used = float(qty)
        since = None
        c = counts.get(iid)
        if c and c["count_day"] == today and f_t > 0.02:
            a_c = a_cache.setdefault(c["counted_at"], float(share_after(db, "petpooja_usage", today, c["counted_at"])))
            used_after = used * max(0.0, min(1.0, (a_c - a_now) / f_t))
            since = c["counted_at"].astimezone(business_tz()).strftime("%I:%M %p").lstrip("0")
        else:
            used_after = used
        # same display unit rule as the Stock Log
        per = (1000.0 / float(st["pack_size_g"])) if (st["item_unit"] == "pcs" and st["pack_size_g"]) else UNIT_FACTOR.get((unit, st["item_unit"]), 1.0)
        raw = float(st["on_hand_qty"])
        full = used / f_t if f_t >= MIN_PROJECT_SHARE else None
        rest = (full - used) if full is not None else None
        opening, now_bal = raw * per, (raw - used_after) * per
        close = now_bal - rest * per if rest is not None else None
        status_, label = classify(now_bal, close, full * per if full else None)
        rows_out.append({"id": iid, "name": st["name"], "unit": st["item_unit"], "category": st["category"] or "Other",
                         "opening": round(opening, 3), "used": round(used_after * per, 3), "since_count": since,
                         "now": round(now_bal, 3), "rest": None if rest is None else round(rest * per, 3),
                         "close": None if close is None else round(close, 3), "status": status_, "label": label})
    order = {"out": 0, "tight": 1, "ok": 2}
    rows_out.sort(key=lambda r: (order[r["status"]], -(r["used"] / r["opening"] if r["opening"] > 0 else 9)))
    out["rows"] = rows_out[:60]
    out["tight"] = sum(1 for r in rows_out if r["status"] == "tight")
    out["out"] = sum(1 for r in rows_out if r["status"] == "out")
    out["day_share"] = round(f_t, 2)
    return out
