"""Live stock: today's sales so far (Petpooja's live Item Report for walk-in /
counter sales, and Zomato's order history for Zomato orders, both read by the
Chrome helper) turned into ingredient use and shown against stock.

A view only. Nothing here touches ingredient_stock: tonight's normal uploads
take the day off stock for real, and a count entered today wins (only the
sales after it come off). Swiggy joins after its own upload.
"""
import datetime
import hashlib
import json
import math
import os
import re
import secrets

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_now, business_tz
from app.core.config import settings
from app.services.sales_stock import Sale, calculate_ingredient_usage, share_after
from app.services.stock_count import HUMAN_ROW_SQL, NON_STOCK_CATEGORIES, NON_STOCK_NAMES, UNIT_FACTOR, count_day
from app.services.uploads.zomato import ITEM_SPLIT_RE, ITEM_TOKEN_RE

STALE_MIN = 30           # a read older than this is shown as stale, not live
MIN_PROJECT_SHARE = 0.25  # before this share of the day has passed, don't project the rest of it
MAX_ROWS = 500
KEEP_DAYS = 4
CHANNELS = ("petpooja", "zomato")
LEDGER = {"petpooja": "petpooja_usage", "zomato": "zomato_usage"}
LABEL = {"petpooja": "Walk-in", "zomato": "Zomato"}
_NOT_COUNTED = re.compile(r"reject|cancel|fail|declin", re.I)  # Zomato order statuses that never became sales
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


def clean_orders(raw) -> list[dict]:
    """Zomato orders from the helper: [{id, status, placed 'YYYY-MM-DDTHH:MM', items '1 x A, 2 x B', total}]."""
    if not isinstance(raw, list):
        raise ValueError("orders must be a list")
    if len(raw) > MAX_ROWS:
        raise ValueError("too many orders")
    out = []
    for o in raw:
        oid = str(o.get("id") or "").strip()
        placed = datetime.datetime.fromisoformat(str(o.get("placed") or ""))
        total = float(str(o.get("total") or 0).replace(",", ""))
        if not oid.isdigit() or not math.isfinite(total):
            raise ValueError(f"bad order: {oid[:20]!r}")
        out.append({"id": oid, "status": str(o.get("status") or "").strip().upper()[:30], "placed": placed,
                    "items": str(o.get("items") or "")[:2000], "total": total})
    return out


def zomato_rows(orders: list[dict], day: datetime.date) -> tuple[list[dict], int, float]:
    """Items sold on Zomato for the business day (1 AM to 1 AM), skipping rejected / cancelled orders.
    Returns ([{name, qty, amount}], orders kept, their total)."""
    start = datetime.datetime.combine(day, datetime.time(1, 0))
    end = start + datetime.timedelta(days=1)
    qty: dict[str, int] = {}
    kept, amount = 0, 0.0
    for o in orders:
        if not (start <= o["placed"] < end) or _NOT_COUNTED.search(o["status"]):
            continue
        kept += 1
        amount += o["total"]
        for tok in ITEM_SPLIT_RE.split(o["items"]):
            m = ITEM_TOKEN_RE.match(tok.strip())
            if m:
                name = m.group(2).strip()
                qty[name] = qty.get(name, 0) + int(m.group(1))
    return [{"name": n, "qty": float(q), "amount": 0.0} for n, q in qty.items()], kept, amount


def save_read(db: Session, token_id: int | None, rows_raw=None, client_read_at: str | None = None,
              channel: str = "petpooja", orders_raw=None) -> dict:
    if channel not in CHANNELS:
        raise ValueError("unknown channel")
    day = count_day(business_now())
    orders_n = None
    if channel == "zomato":
        rows, orders_n, amount = zomato_rows(clean_orders(orders_raw), day)
        dishes = sum(r["qty"] for r in rows)
    else:
        rows = clean_rows(rows_raw)
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
        INSERT INTO live_sales_reads (business_date, client_read_at, rows, dishes, amount, token_id, channel, orders_n)
        VALUES (:d, :c, CAST(:r AS jsonb), :n, :a, :t, :ch, :on)"""),
        {"d": day, "c": client_at, "r": json.dumps(rows), "n": dishes, "a": amount, "t": token_id, "ch": channel, "on": orders_n})
    db.execute(text("DELETE FROM live_sales_reads WHERE business_date < :d"), {"d": day - datetime.timedelta(days=KEEP_DAYS)})
    return {"business_date": day.isoformat(), "channel": channel, "rows": len(rows), "orders": orders_n,
            "dishes": dishes, "amount": amount}


# ── the live view ─────────────────────────────────────────────────────────────

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


def _resolvers(db: Session):
    """name -> (canonical menu name, MenuItem) for each channel: Petpooja's names go through the
    nightly upload's alias map, Zomato's through the Zomato upload's item matcher."""
    from app.models.menu_item import MenuItem
    from app.services.uploads.item_matching import MenuIndex
    from scripts.import_pos import build_resolver, pos_alias_map
    with open(_SEED, encoding="utf-8") as f:
        seed = json.load(f)
    items = db.query(MenuItem).all()
    menu = {m.name: m for m in items}
    by_id = {m.id: m for m in items}
    pos = build_resolver(seed, menu, pos_alias_map(db))
    zidx = MenuIndex(db)

    def petpooja(name):
        canon = pos.get(name)
        return (canon, menu.get(canon)) if canon else (None, None)

    def zomato(name):
        mid = zidx.match(name)
        item = by_id.get(mid) if mid else None
        if item is None:  # names the Zomato matcher can't tell apart (Gobi Manchurian: Gravy or Starter?) follow the links made on Menu Analysis
            canon = pos.get(name)
            item = menu.get(canon) if canon else None
        return (item.name, item) if item else (None, None)
    return {"petpooja": petpooja, "zomato": zomato}


def _part(ch: str, reads: list, resolve, now: datetime.datetime, today: datetime.date) -> dict | None:
    """One channel's side of the view from its reads today (oldest first)."""
    if not reads:
        return None
    last = reads[-1]
    sales, unmapped, merged, dishes = [], [], {}, 0.0
    for r in last["rows"]:
        canon, item = resolve(r["name"])
        if item is None:
            unmapped.append({"name": r["name"], "qty": r["qty"], "channel": LABEL[ch]})
            continue
        sales.append(Sale(canon, r["qty"], today))
        if getattr(item, "is_food", True):                      # Parcel / Water Bottle aren't dishes
            dishes += r["qty"]
            merged[canon] = merged.get(canon, 0) + r["qty"]
    # dishes by hour, from the gaps between reads (the day's first read has nothing to compare with)
    hours: dict[int, float] = {}
    prev = float(reads[0]["dishes"])
    for rd in reads[1:]:
        h = rd["read_at"].astimezone(business_tz()).hour
        h = h if h >= 5 else h + 24
        hours[h] = hours.get(h, 0.0) + max(float(rd["dishes"]) - prev, 0.0)
        prev = float(rd["dishes"])
    # How long the totals have stood still: Petpooja syncs in batches and can run behind the till.
    still_since = last["read_at"]
    for rd in reversed(reads[:-1]):
        if float(rd["dishes"]) == float(last["dishes"]) and float(rd["amount"]) == float(last["amount"]):
            still_since = rd["read_at"]
        else:
            break
    age = max(0.0, (now - last["read_at"]).total_seconds() / 60)
    return {"ch": ch, "read_at": last["read_at"], "age_min": age, "status": "live" if age <= STALE_MIN else "stale",
            "reads": len(reads), "dishes": dishes, "amount": float(last["amount"]), "orders": last["orders_n"],
            "sales": sales, "merged": merged, "unmapped": unmapped, "hours": hours,
            "still_since": still_since, "still_min": max(0.0, (last["read_at"] - still_since).total_seconds() / 60)}


def _summary(p: dict | None) -> dict | None:
    if not p:
        return None
    return {"read_at": _fmt_dt(p["read_at"]), "age_min": round(p["age_min"], 1), "status": p["status"], "reads": p["reads"],
            "dishes": p["dishes"], "amount": p["amount"], "orders": p["orders"],
            "still_since": _fmt_dt(p["still_since"]), "still_min": round(p["still_min"], 1)}


def snapshot(db: Session) -> dict:
    now = business_now()
    today = count_day(now)
    all_reads = db.execute(text("""
        SELECT id, channel, read_at, rows, dishes, amount, orders_n FROM live_sales_reads
         WHERE business_date = :d ORDER BY read_at, id"""), {"d": today}).mappings().all()
    base = {"today": today.isoformat(), "now": _fmt_dt(now), "has_helper": bool(tokens(db))}
    if not all_reads:
        return {**base, "status": "none"}

    resolvers = _resolvers(db)
    parts = {ch: _part(ch, [r for r in all_reads if r["channel"] == ch], resolvers[ch], now, today) for ch in CHANNELS}
    live = [p for p in parts.values() if p]
    primary = parts["petpooja"] or parts["zomato"]

    merged: dict[str, float] = {}
    hours: dict[int, float] = {}
    for p in live:
        for n, q in p["merged"].items():
            merged[n] = merged.get(n, 0) + q
        for h, n in p["hours"].items():
            hours[h] = hours.get(h, 0.0) + n
    freshest = min(live, key=lambda p: p["still_min"])
    notices: list[str] = []
    out = {**base, "status": primary["status"], "read_at": _fmt_dt(max(p["read_at"] for p in live)),
           "age_min": round(min(p["age_min"] for p in live), 1), "reads_today": sum(p["reads"] for p in live),
           "dishes": sum(p["dishes"] for p in live), "amount": sum(p["amount"] for p in live),
           "walkin": _summary(parts["petpooja"]), "zomato": _summary(parts["zomato"]),
           "still_since": _fmt_dt(freshest["still_since"]), "still_min": round(freshest["still_min"], 1),
           "top": [{"name": n, "qty": q} for n, q in sorted(merged.items(), key=lambda kv: -kv[1])],
           "unmapped": [u for p in live for u in p["unmapped"]],
           "hours": [{"h": h, "n": round(n, 1)} for h, n in sorted(hours.items())],
           "rows": [], "notice": None, "notices": notices, "tight": 0, "out": 0}
    for p in live:  # a channel that went quiet is named, not hidden behind the other one's fresh read
        if p["status"] == "stale" and p is not primary:
            notices.append(f"The last {LABEL[p['ch']]} reading is {round(p['age_min'])} minutes old, so {LABEL[p['ch']]} sales below are as of "
                           f"{p['read_at'].astimezone(business_tz()).strftime('%I:%M %p').lstrip('0')}, not now.")

    use_parts, ingested_any = [], False
    for p in live:
        done = db.execute(text("SELECT 1 FROM ingredient_stock WHERE note LIKE :p LIMIT 1"),
                          {"p": f"{LEDGER[p['ch']]}:{today.isoformat()}:%"}).first()
        if done:
            ingested_any = True
            notices.append(f"Today's {LABEL[p['ch']]} sales have already been taken off stock, so they're left out here to avoid counting them twice.")
        elif p["sales"]:
            use_parts.append(p)
    out["notice"] = " ".join(notices) or None
    if not use_parts:
        return {**out, "status": "ingested"} if ingested_any else out

    per_item: dict[int, dict[str, tuple[float, str]]] = {}
    for p in use_parts:
        usage, _ = calculate_ingredient_usage(db, p["sales"])
        for (_, iid), (qty, unit) in usage.items():
            per_item.setdefault(iid, {})[p["ch"]] = (float(qty), unit)
    ids = sorted(per_item)
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

    # per channel: share of the day still to come at its read time (a_now) and already behind us (f_t)
    a_now = {p["ch"]: float(share_after(db, LEDGER[p["ch"]], today, p["read_at"])) for p in use_parts}
    f_t = {ch: max(1 - a, 0.0) for ch, a in a_now.items()}
    a_cache: dict[tuple, float] = {}

    rows_out = []
    for iid, chans in per_item.items():
        st = stock.get(iid)
        if not st or not st["is_active"] or (st["category"] or "") in NON_STOCK_CATEGORIES or st["name"] in NON_STOCK_NAMES:
            continue
        if any(unit != st["stock_unit"] for _, unit in chans.values()):
            continue  # balance in another unit than the usage: nothing sensible to subtract
        c = counts.get(iid)
        counted_today = bool(c and c["count_day"] == today)
        used_total = used_after_total = 0.0
        full_total: float | None = 0.0
        for ch, (used, _) in chans.items():
            used_total += used
            if counted_today and f_t[ch] > 0.02:
                key = (ch, c["counted_at"])
                a_c = a_cache.setdefault(key, float(share_after(db, LEDGER[ch], today, c["counted_at"])))
                used_after_total += used * max(0.0, min(1.0, (a_c - a_now[ch]) / f_t[ch]))
            else:
                used_after_total += used
            if full_total is not None:
                full_total = full_total + used / f_t[ch] if f_t[ch] >= MIN_PROJECT_SHARE else None
        since = c["counted_at"].astimezone(business_tz()).strftime("%I:%M %p").lstrip("0") if counted_today else None
        # same display unit rule as the Stock Log
        per = (1000.0 / float(st["pack_size_g"])) if (st["item_unit"] == "pcs" and st["pack_size_g"]) else UNIT_FACTOR.get((st["stock_unit"], st["item_unit"]), 1.0)
        raw = float(st["on_hand_qty"])
        rest = (full_total - used_total) if full_total is not None else None
        opening, now_bal = raw * per, (raw - used_after_total) * per
        close = now_bal - rest * per if rest is not None else None
        status_, label = classify(now_bal, close, full_total * per if full_total else None)
        rows_out.append({"id": iid, "name": st["name"], "unit": st["item_unit"], "category": st["category"] or "Other",
                         "opening": round(opening, 3), "used": round(used_after_total * per, 3), "since_count": since,
                         "now": round(now_bal, 3), "rest": None if rest is None else round(rest * per, 3),
                         "close": None if close is None else round(close, 3), "status": status_, "label": label})
    order = {"out": 0, "tight": 1, "ok": 2}
    rows_out.sort(key=lambda r: (order[r["status"]], -(r["used"] / r["opening"] if r["opening"] > 0 else 9)))
    out["rows"] = rows_out[:60]
    out["tight"] = sum(1 for r in rows_out if r["status"] == "tight")
    out["out"] = sum(1 for r in rows_out if r["status"] == "out")
    out["day_share"] = round(f_t.get("petpooja", next(iter(f_t.values()))), 2)
    return out
