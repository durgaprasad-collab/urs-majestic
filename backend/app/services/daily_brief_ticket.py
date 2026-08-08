"""Live Daily Brief (ticket-rail redesign) — data assembly only.

Five role panels (Restaurant GM / COO / CRO-BizDev / Creative Director /
BI Manager) over the same financial core the rest of the OS already uses:
revenue from v_ceo_brief_summary, break-even/target from target_engine
(fixed_expenses + business_settings), per-role metrics from business_settings
and menu_items, and a live task list per role pulled from the Notion task
board ("URS Majestic — Task Board RESET (Aug 2026)").

Confidence-ink tiers (measured | derived | assumed) are PARSED from each
business_settings row's own `note` text, never hand-mapped — the note-tagging
convention (MEASURED / ACTUAL .. weighed / DERIVED .. NOT MEASURED /
ASSUMPTION .. not measured / Modelled .. UNSOURCED / RETIRED) already exists
in the data; a second hand-maintained classification would drift from it the
first time someone adds a new setting.

Notion calls use urllib (matching receipt_parse.py's OCR.space pattern) --
no new HTTP dependency. Every Notion call degrades gracefully: an unset
NOTION_API_KEY or a Notion-side failure returns a `notion_connected: False`
panel state rather than breaking the page.
"""
import datetime
import decimal
import json
import urllib.error
import urllib.request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.clock import business_today
from app.services import business_settings as bs
from app.services import target_engine
from app.services.ceo_brief import get_summary as get_revenue_strip  # re-exported

D = decimal.Decimal

# Gas moved from a per-piece variable cost to a flat kitchen fixed-cost line
# on this date (see business_settings 'tandoor_fuel_cost_per_piece' RETIRED
# note and 'kitchen_gas_kg_per_day' history). contribution_margin_pct has not
# been recomputed since, so any margin row still effective before this date
# is stale by construction, not by guesswork.
_GAS_FIXED_COST_CUTOVER = datetime.date(2026, 7, 28)

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"

# business_settings keys the GM ticket reads, in display order.
_GM_PORTION_KEYS = [
    "atta_g_per_piece", "naan_dough_g_per_piece", "dough_g_per_piece",
    "maida_g_per_piece", "stuffing_g_per_piece", "cashew_g_per_gravy_portion",
]
# COO reads the same business_settings convention for gas.
_COO_GAS_KEY = "kitchen_gas_kg_per_day"


# ── confidence-ink tier parser ──────────────────────────────────────────────

def _confidence_tier(note: str | None) -> str:
    """measured | derived | assumed, parsed from the note's own leading
    vocabulary -- the convention business_settings already writes in.

    Order matters, most-specific first:
      1. 'DERIVED' is checked BEFORE the bare 'NOT MEASURED' catch, so
         'DERIVED, NOT MEASURED' (the calculation IS the source) reads as
         derived, not assumed -- 'NOT MEASURED' there qualifies *how* it
         was derived, it isn't a bare guess.
      2. RETIRED / ASSUMPTION / UNSOURCED / a bare 'NOT MEASURED' or
         'NOT WEIGHED' (no DERIVED nearby) -> assumed.
      3. MEASURED / ACTUAL / WEIGHED -> measured.
      4. MODELLED / CORRECTED (a calculation, without an explicit DERIVED
         label) -> derived.
      5. No sourcing vocabulary at all -> assumed (weakest tier by default,
         never claimed up rather than left unclassified)."""
    if not note:
        return "assumed"
    n = note.upper()
    if "DERIVED" in n:
        return "derived"
    if n.startswith("RETIRED") or "ASSUMPTION" in n or "UNSOURCED" in n or "NOT MEASURED" in n or "NOT WEIGHED" in n:
        return "assumed"
    if "MEASURED" in n or "ACTUAL" in n or "WEIGHED" in n:
        return "measured"
    if "MODELLED" in n or "MODELED" in n or "CORRECTED" in n:
        return "derived"
    return "assumed"


def _setting_full(db: Session, key: str, as_of: datetime.date | None = None) -> dict | None:
    """Current row for `key` (value + note + confidence), or None if never set."""
    as_of = as_of or business_today()
    row = db.execute(
        text(
            """
            SELECT value, note, effective_from
            FROM business_settings
            WHERE setting_key = :key AND effective_from <= :as_of
            ORDER BY effective_from DESC, id DESC
            LIMIT 1
            """
        ),
        {"key": key, "as_of": as_of},
    ).mappings().first()
    if row is None:
        return None
    return {
        "key": key,
        "value": D(str(row["value"])),
        "note": row["note"],
        "effective_from": row["effective_from"],
        "confidence": _confidence_tier(row["note"]),
    }


# ── target line (break-even floor + staleness flag) ─────────────────────────

def get_target_line(db: Session, *, reporting_date: datetime.date, mtd: decimal.Decimal,
                     days_elapsed: int) -> dict:
    """Wraps target_engine.compute() (the existing, single source of truth for
    break-even/operating/stretch) and adds the deliberate margin-staleness
    flag. Does NOT silently recompute a target off a stale margin -- the flag
    stays up until a human writes a new contribution_margin_pct row."""
    targets = target_engine.compute(db, mtd=mtd, reporting_date=reporting_date, days_elapsed=days_elapsed)
    margin_row = _setting_full(db, bs.SETTING_CONTRIBUTION_MARGIN_PCT, as_of=reporting_date)
    margin_stale = bool(margin_row and margin_row["effective_from"] < _GAS_FIXED_COST_CUTOVER)
    targets["margin_stale"] = margin_stale
    targets["margin_stale_reason"] = (
        f"contribution_margin_pct last set {margin_row['effective_from']:%d %b %Y} "
        f"— before gas moved to a fixed cost on {_GAS_FIXED_COST_CUTOVER:%d %b %Y}. "
        "NEEDS RECOMPUTE."
    ) if margin_stale else None
    return targets


# ── GM panel ─────────────────────────────────────────────────────────────────

def get_gm_panel(db: Session) -> dict:
    metrics = []
    for key in _GM_PORTION_KEYS:
        row = _setting_full(db, key)
        if row is None:
            continue
        metrics.append({
            "label": key.replace("_", " "),
            "value": f"{row['value']:g} g",
            "confidence": row["confidence"],
            "note": row["note"],
        })
    return {"role": "gm", "metrics": metrics}


# ── COO panel ────────────────────────────────────────────────────────────────

def get_coo_panel(db: Session) -> dict:
    gas = _setting_full(db, _COO_GAS_KEY)
    metrics = []
    if gas:
        metrics.append({
            "label": "Kitchen gas / day",
            "value": f"{gas['value']:g} kg",
            "confidence": gas["confidence"],
            "note": gas["note"],
        })

    # Combo pricing sync: menu_items.price vs each item's most recent
    # confirmed live price, if that history is tracked anywhere. No
    # dedicated "live price" table exists yet, so this ships as a same-price
    # sanity list (flags nothing today) rather than inventing a comparison
    # source that doesn't exist -- see note in the daily-brief route.
    combo_rows = db.execute(
        text("SELECT id, name, price FROM menu_items WHERE is_active AND category = 'Combos'")
    ).mappings().all()
    metrics.append({
        "label": "Combo items tracked",
        "value": str(len(combo_rows)),
        "confidence": "measured",
        "note": "No live-price-history table exists yet to diff against — "
                "this counts tracked combos only, it does not yet detect mismatches.",
    })
    return {"role": "coo", "metrics": metrics}


# ── BI panel ─────────────────────────────────────────────────────────────────

def get_bi_panel(db: Session) -> dict:
    rows = db.execute(
        text(
            "SELECT cost_confidence, count(*) FROM menu_items "
            "WHERE is_active AND is_food GROUP BY cost_confidence"
        )
    ).all()
    counts = {r[0]: r[1] for r in rows}
    summary = get_revenue_strip(db) or {}
    return {
        "role": "bi",
        "metrics": [
            {"label": "Reliable cost", "value": str(counts.get("reliable", 0)), "confidence": "measured"},
            {"label": "Building cost", "value": str(counts.get("building", 0)), "confidence": "derived"},
            {"label": "No cost data", "value": str(counts.get("none", 0)), "confidence": "assumed"},
            {"label": "Unexplained mismatches", "value": str(summary.get("unexplained_mismatches", 0)),
             "confidence": "measured"},
        ],
    }


# ── Creative panel (Google review count — manual entry, no API) ────────────

_GOOGLE_REVIEW_COUNT_KEY = "google_review_count"


def get_creative_panel(db: Session) -> dict:
    row = _setting_full(db, _GOOGLE_REVIEW_COUNT_KEY)
    return {
        "role": "creative",
        "metrics": [{
            "label": "Google reviews",
            "value": str(int(row["value"])) if row else "—",
            "confidence": "measured" if row else "assumed",
            "note": row["note"] if row else "Not yet entered — manual field, no Google API connected.",
            "editable_key": _GOOGLE_REVIEW_COUNT_KEY,
        }],
    }


def set_google_review_count(db: Session, count: int, entered_by: str) -> None:
    bs.set_setting(
        db, _GOOGLE_REVIEW_COUNT_KEY, count,
        note=f"MEASURED, manual entry by {entered_by} — no Google API connected.",
        created_by=entered_by,
    )


# ── Notion task board (per-role tasks, all 5 panels) ────────────────────────

def notion_connected() -> bool:
    return bool(settings.NOTION_API_KEY)


def _notion_request(method: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{_NOTION_API}{path}",
        data=json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {settings.NOTION_API_KEY}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def get_role_tasks(db: Session, role_label: str) -> dict:
    """{connected, source, tasks: [...]} for one Notion 'Role' value.

    Two paths, chosen automatically:
      - NOTION_API_KEY set  -> live Notion query (source='live'). This is the
        real, final path -- once the credential exists this activates with
        no further code changes.
      - NOTION_API_KEY unset -> read the notion_task_cache table instead
        (source='cache'), an interim bridge: Claude refreshes that table from
        its own interactive Notion connector (refresh_task_cache below) since
        the deployed app can't reach Notion on its own yet.
    Never raises -- a Notion hiccup or an empty/stale cache degrades to an
    empty task list, not a broken page."""
    if notion_connected():
        try:
            payload = _notion_request(
                "POST", f"/data_sources/{settings.NOTION_TASK_BOARD_DATA_SOURCE_ID}/query",
                {
                    "filter": {
                        "and": [
                            {"property": "Role", "select": {"equals": role_label}},
                            {"property": "Status", "select": {"does_not_equal": "Done"}},
                        ]
                    },
                    "sorts": [{"property": "Priority", "direction": "ascending"}],
                },
            )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            return {"connected": True, "source": "live", "tasks": [], "error": str(e), "cached_at": None}

        tasks = []
        for page in payload.get("results", []):
            p = page.get("properties", {})
            title_parts = p.get("Task", {}).get("title", [])
            tasks.append({
                "id": page["id"],
                "task": "".join(t.get("plain_text", "") for t in title_parts),
                "done_means": _rich_text(p.get("Done Means")),
                "kill_criterion": _rich_text(p.get("Kill Criterion")),
                "priority": (p.get("Priority", {}).get("select") or {}).get("name"),
            })
        return {"connected": True, "source": "live", "tasks": tasks, "error": None, "cached_at": None}

    rows = db.execute(
        text(
            """
            SELECT notion_page_id, task, done_means, kill_criterion, priority,
                   max(cached_at) OVER () AS cached_at
            FROM notion_task_cache
            WHERE role = :role AND NOT local_done
            ORDER BY priority NULLS LAST, id
            """
        ),
        {"role": role_label},
    ).mappings().all()
    tasks = [{
        "id": r["notion_page_id"], "task": r["task"], "done_means": r["done_means"],
        "kill_criterion": r["kill_criterion"], "priority": r["priority"],
    } for r in rows]
    cached_at = rows[0]["cached_at"] if rows else None
    return {"connected": True, "source": "cache", "tasks": tasks, "error": None, "cached_at": cached_at}


def _rich_text(prop: dict | None) -> str:
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))


def mark_task_done(db: Session, page_id: str, done_by: str) -> tuple[bool, str | None]:
    """Mark a task done. Live path PATCHes Notion directly. Cache path marks
    the row done locally (instant UI feedback) and queues it in
    synced_to_notion=false for Claude to push to Notion afterward via
    pending_notion_sync()/mark_synced() -- see the module docstring."""
    if notion_connected():
        try:
            _notion_request(
                "PATCH", f"/pages/{page_id}",
                {"properties": {"Status": {"select": {"name": "Done"}}}},
            )
            return True, None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            return False, str(e)

    result = db.execute(
        text(
            """
            UPDATE notion_task_cache
            SET local_done = true, local_done_at = now(), local_done_by = :by, synced_to_notion = false
            WHERE notion_page_id = :pid
            """
        ),
        {"pid": page_id, "by": done_by},
    )
    db.commit()
    if result.rowcount == 0:
        return False, "Task not found in local cache."
    return True, None


def refresh_task_cache(db: Session, rows: list[dict]) -> int:
    """Upsert Notion task rows into the local cache. `rows` items:
    {notion_page_id, role, task, done_means, kill_criterion, priority, status}.
    Called from an interactive session using Claude's own Notion connector --
    the deployed app never calls this itself. A row whose Notion status is
    now 'Done' is upserted as local_done=True too, so a completion made
    directly in Notion (not through the brief) also disappears from the list."""
    n = 0
    for r in rows:
        is_done = (r.get("status") or "").strip().lower() == "done"
        db.execute(
            text(
                """
                INSERT INTO notion_task_cache
                    (notion_page_id, role, task, done_means, kill_criterion, priority,
                     notion_status, cached_at, local_done, synced_to_notion)
                VALUES
                    (:pid, :role, :task, :done_means, :kill, :priority, :status, now(), :done, true)
                ON CONFLICT (notion_page_id) DO UPDATE SET
                    role = EXCLUDED.role, task = EXCLUDED.task, done_means = EXCLUDED.done_means,
                    kill_criterion = EXCLUDED.kill_criterion, priority = EXCLUDED.priority,
                    notion_status = EXCLUDED.notion_status, cached_at = now(),
                    -- Don't clobber a pending local completion that hasn't synced yet.
                    local_done = notion_task_cache.local_done OR EXCLUDED.local_done,
                    synced_to_notion = CASE WHEN notion_task_cache.synced_to_notion THEN true ELSE notion_task_cache.synced_to_notion END
                """
            ),
            {
                "pid": r["notion_page_id"], "role": r["role"], "task": r["task"],
                "done_means": r.get("done_means"), "kill": r.get("kill_criterion"),
                "priority": r.get("priority"), "status": r.get("status"), "done": is_done,
            },
        )
        n += 1
    db.commit()
    return n


def pending_notion_sync(db: Session) -> list[dict]:
    """Cache rows marked done locally but not yet pushed to real Notion --
    what Claude's next sync pass needs to PATCH."""
    rows = db.execute(
        text(
            "SELECT notion_page_id, role, task, local_done_by, local_done_at "
            "FROM notion_task_cache WHERE local_done AND NOT synced_to_notion ORDER BY local_done_at"
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def mark_synced(db: Session, notion_page_id: str) -> None:
    db.execute(
        text("UPDATE notion_task_cache SET synced_to_notion = true WHERE notion_page_id = :pid"),
        {"pid": notion_page_id},
    )
    db.commit()


# Maps the frontend's role slug to the exact Notion select-option text.
ROLE_LABELS = {
    "gm": "Restaurant GM",
    "coo": "COO",
    "cro": "CRO / BizDev",
    "creative": "Creative Director",
    "bi": "BI Manager",
}

_PANEL_BUILDERS = {
    "gm": get_gm_panel,
    "coo": get_coo_panel,
    "bi": get_bi_panel,
    "creative": get_creative_panel,
}


def build_ticket_brief(db: Session) -> dict:
    """Full context for the ticket-rail /daily-brief page: revenue strip,
    target line (with staleness flag), and all five role panels, each with
    its own metrics plus a live Notion task list."""
    summary = get_revenue_strip(db)
    if not summary:
        return {"has_data": False}

    # Reuses daily_brief_v3's own sales-series/MTD computation rather than a
    # second implementation of the same sum -- one series read, one convention.
    from app.services.daily_brief_v3 import _sales_series
    rep = summary["data_through"]
    series = _sales_series(db, rep)
    month_start = rep.replace(day=1)
    mtd = sum((v for d, v in series.items() if d >= month_start), D("0"))

    target = get_target_line(db, reporting_date=rep, mtd=mtd, days_elapsed=rep.day)

    roles = {}
    for slug, label in ROLE_LABELS.items():
        builder = _PANEL_BUILDERS.get(slug)
        panel = builder(db) if builder else {"role": slug, "metrics": []}
        panel["label"] = label
        panel["tasks"] = get_role_tasks(db, label)
        roles[slug] = panel

    return {
        "has_data": True,
        "summary": summary,
        "target": target,
        "roles": roles,
        "reporting_date": rep,
        "notion_connected": notion_connected(),
    }
