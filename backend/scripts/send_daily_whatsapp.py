"""Daily WhatsApp brief — tomorrow's prep sheet + ingredients due to order.

Sends one WhatsApp *template* message per recipient via the Meta WhatsApp Cloud
API. Two Render Cron Jobs (schedules are UTC; IST = UTC+5:30):

    Midday brief (prep + order)   30 7  * * *   # 1 PM IST
        python -m scripts.send_daily_whatsapp

    Evening order forecast only   30 14 * * *   # 8 PM IST
        python -m scripts.send_daily_whatsapp --only order

Why a template (not free text): WhatsApp business-INITIATED messages outside the
24h customer window must use a pre-approved template. Template variable values
also cannot contain newlines/tabs, so each list here is squeezed onto a single
separator-delimited line; the line breaks live in the template's fixed text.

Two approved Meta templates are needed:

  WHATSAPP_TEMPLATE_NAME (default 'daily_brief') — 3 params {{1}}/{{2}}/{{3}}:
    URS Majestic — Daily Brief
    Prep for {{1}}: {{2}}
    To order now: {{3}}

  WHATSAPP_ORDER_TEMPLATE_NAME (default 'order_forecast') — 2 params, used by
  --only order:
    URS Majestic — Order Forecast ({{1}})
    To order now: {{2}}

Safe by default: with WHATSAPP_TOKEN / WHATSAPP_PHONE_ID unset (or with
--dry-run) it prints the exact params and sends nothing — so it can be committed
and even scheduled before the Meta setup is finished.

    python -m scripts.send_daily_whatsapp --dry-run
    python -m scripts.send_daily_whatsapp --date 2026-07-22   # override prep day
"""
import argparse
import datetime
import json
import math
import sys
import urllib.error
import urllib.request

from sqlalchemy import text

from app.core.clock import business_today
from app.core.config import settings
from app.core.database import SessionLocal

_PARAM_MAX = 900          # keep each body param well under WhatsApp's ~1024 cap
_KITCHEN_BANDS = ["core", "occasional"]


def _clean(s: str) -> str:
    """WhatsApp template params reject newlines/tabs and >4 consecutive spaces."""
    return " ".join(str(s).split())


def _fmt_qty(q) -> str:
    """Round UP to a whole unit — staff can't buy "926.67ml" off a physical
    list. Note this can overstate kg-scale ingredients by up to ~1 unit
    (1.71kg -> 2kg); fine for a purchase nudge, not a precision order qty."""
    return str(math.ceil(float(q))) if q is not None else "?"


def _clip(s: str) -> str:
    s = _clean(s)
    return s if len(s) <= _PARAM_MAX else s[: _PARAM_MAX - 1].rstrip() + "…"


def _prep_summary(db, target: datetime.date) -> str:
    """Top prep items for `target`'s weekday, from v_prep_sheet (kitchen bands)."""
    dow = int(target.strftime("%w"))
    rows = db.execute(
        text(
            "SELECT p.item, p.prep_qty_suggested "
            "FROM v_prep_sheet p JOIN menu_items m ON m.id = p.menu_item_id "
            "WHERE p.dow = :dow AND p.demand_band = ANY(:bands) "
            "  AND m.is_food AND p.prep_qty_suggested > 0 "
            "ORDER BY p.prep_qty_suggested DESC, p.avg_7d DESC, p.item "
            "LIMIT :lim"
        ),
        {"dow": dow, "bands": _KITCHEN_BANDS, "lim": settings.WHATSAPP_PREP_LIMIT},
    ).all()
    if not rows:
        return "nothing flagged"
    return " · ".join(f"{_clean(r.item)} {int(r.prep_qty_suggested)}" for r in rows)


def _order_summary(db) -> str:
    """Ingredients due/overdue now, skipping anything just purchased."""
    rows = db.execute(
        text(
            "SELECT name, suggested_order_qty, unit, status "
            "FROM v_ingredient_reorder_forecast "
            "WHERE is_active AND status IN ('overdue', 'due') AND NOT recently_purchased "
            "ORDER BY CASE status WHEN 'overdue' THEN 0 ELSE 1 END, days_until_due, name "
            "LIMIT :lim"
        ),
        {"lim": settings.WHATSAPP_ORDER_LIMIT},
    ).all()
    if not rows:
        return "nothing due"
    return ", ".join(f"{_clean(r.name)} {_fmt_qty(r.suggested_order_qty)}{r.unit}" for r in rows)


def _send(to: str, params: list[str], template: str) -> tuple[bool, str]:
    url = (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        f"/{settings.WHATSAPP_PHONE_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            ],
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, resp.read().decode()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()[:300]}"
    except Exception as e:  # network / DNS / timeout
        return False, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description="Send the daily WhatsApp prep + order brief.")
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    ap.add_argument("--date", help="prep target date YYYY-MM-DD (default: tomorrow IST)")
    ap.add_argument(
        "--only", choices=["both", "order"], default="both",
        help="'order' sends just the reorder list (evening run), using the "
             "order-only template; default 'both' sends prep + order.",
    )
    args = ap.parse_args()

    # Windows consoles default to cp1252; the message uses "·". Never let a
    # console encoding crash the job (Render is already UTF-8; this is a no-op there).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    today = business_today()
    target = today + datetime.timedelta(days=1)
    if args.date:
        target = datetime.date.fromisoformat(args.date)

    db = SessionLocal()
    try:
        if args.only == "order":
            # Evening run: what's due to order right now. Dated today (when the
            # order is actually placed), not tomorrow.
            template = settings.WHATSAPP_ORDER_TEMPLATE_NAME
            date_label = today.strftime("%a %d %b")
            params = [date_label, _clip(_order_summary(db))]
            shown = [("{{1}} date ", date_label), ("{{2}} order", params[1])]
        else:
            template = settings.WHATSAPP_TEMPLATE_NAME
            date_label = target.strftime("%a %d %b")
            params = [date_label, _clip(_prep_summary(db, target)), _clip(_order_summary(db))]
            shown = [("{{1}} date ", date_label), ("{{2}} prep ", params[1]),
                     ("{{3}} order", params[2])]
    finally:
        db.close()

    recipients = [n.strip() for n in settings.WHATSAPP_RECIPIENTS.split(",") if n.strip()]

    print(f"== WhatsApp {'order forecast' if args.only == 'order' else 'daily brief'} ==========")
    print(f"Template   : {template} ({settings.WHATSAPP_TEMPLATE_LANG})")
    for label, value in shown:
        print(f"{label}: {value}")
    print(f"Recipients : {', '.join(recipients) or '(none configured)'}")

    configured = bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_ID)
    if args.dry_run or not configured:
        why = "--dry-run" if args.dry_run else "WHATSAPP_TOKEN/PHONE_ID not set"
        print(f"\n[{why}] nothing sent.")
        return 0

    if not recipients:
        print("\nNo recipients configured (WHATSAPP_RECIPIENTS). Nothing sent.")
        return 1

    failures = 0
    for to in recipients:
        ok, resp = _send(to, params, template)
        print(f"  -> {to}: {'SENT' if ok else 'FAILED'} - {resp[:200]}")
        failures += 0 if ok else 1
    print(f"\nDone: {len(recipients) - failures}/{len(recipients)} sent.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
