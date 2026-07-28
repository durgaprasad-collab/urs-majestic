"""Daily Telegram brief — tomorrow's prep sheet + ingredients due to order.

Sibling to send_daily_whatsapp.py, built for the same two Render Cron Jobs
(schedules are UTC; IST = UTC+5:30):

    Midday brief (prep + order)   30 7  * * *   # 1 PM IST
        python -m scripts.send_daily_telegram

    Evening order forecast only   30 14 * * *   # 8 PM IST
        python -m scripts.send_daily_telegram --only order

Why Telegram instead of / alongside WhatsApp: this is an internal ops
broadcast (owner + kitchen), not a customer-facing message. Meta's Cloud API
has no "internal" category — it silently classified daily_brief/order_forecast
as Marketing on 23 Jul 2026 because the copy doesn't match any Utility
sub-type, which means per-conversation cost, opt-in/quality-rating exposure,
and a review queue, for a message nobody replies to. A Telegram bot has none
of that: no template pre-approval, no per-message cost, no category games —
just free-form text to any chat that has started the bot. Trade-off: everyone
who should receive it must open the bot once first (see SETUP below); after
that it's simpler and cheaper than WhatsApp for this exact use case.

Unlike WhatsApp template params, Telegram messages aren't capped at ~1024
chars with no newlines — so this renders each list as real bullet lines
instead of a squeezed single-line summary.

SETUP (one-time, do this yourself — Claude can't do it for you):
  1. Message @BotFather on Telegram, send /newbot, pick a name + username.
     BotFather replies with a token like "123456789:AAExxxxxxxxxxxxxxxxxxxxx".
     Set that as TELEGRAM_BOT_TOKEN.
  2. Each recipient (you + kitchen) opens a chat with the new bot and sends
     it any message (e.g. "hi") — Telegram bots cannot message a chat that
     hasn't messaged them first.
  3. For each recipient, get their numeric chat ID: message @userinfobot
     (or @getidsbot) from that same Telegram account — it replies with the
     numeric ID immediately. Collect all of them into TELEGRAM_CHAT_IDS,
     comma-separated.

Safe by default: with TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS unset (or with
--dry-run) it prints the exact message and sends nothing — so it can be
committed and even scheduled before the bot setup is finished.

    python -m scripts.send_daily_telegram --dry-run
    python -m scripts.send_daily_telegram --date 2026-07-22   # override prep day
"""
import argparse
import datetime
import html
import json
import math
import sys
import urllib.error
import urllib.request

from sqlalchemy import text

from app.core.clock import business_today
from app.core.config import settings
from app.core.database import SessionLocal

_MSG_MAX = 3500          # headroom under Telegram's 4096-char message cap
_KITCHEN_BANDS = ["core", "occasional"]


def _esc(s) -> str:
    """Telegram HTML parse_mode chokes on raw &, <, > — escape every value
    pulled from the DB before it goes into the message."""
    return html.escape(str(s), quote=False)


def _fmt_qty(q) -> str:
    """Round UP to a whole unit — staff can't buy "926.67ml" off a physical
    list. Note this can overstate kg-scale ingredients by up to ~1 unit
    (1.71kg -> 2kg); fine for a purchase nudge, not a precision order qty."""
    return str(math.ceil(float(q))) if q is not None else "?"


def _prep_lines(db, target: datetime.date) -> list[str]:
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
        {"dow": dow, "bands": _KITCHEN_BANDS, "lim": settings.TELEGRAM_PREP_LIMIT},
    ).all()
    return [f"• {_esc(r.item)} — {int(r.prep_qty_suggested)}" for r in rows]


def _order_lines(db) -> list[str]:
    """Ingredients due/overdue now, skipping anything just purchased."""
    rows = db.execute(
        text(
            "SELECT name, suggested_order_qty, unit, status "
            "FROM v_ingredient_reorder_forecast "
            "WHERE is_active AND status IN ('overdue', 'due') AND NOT recently_purchased "
            "ORDER BY CASE status WHEN 'overdue' THEN 0 ELSE 1 END, days_until_due, name "
            "LIMIT :lim"
        ),
        {"lim": settings.TELEGRAM_ORDER_LIMIT},
    ).all()
    marker = {"overdue": " (overdue)"}
    return [
        f"• {_esc(r.name)} — {_fmt_qty(r.suggested_order_qty)}{_esc(r.unit)}"
        f"{marker.get(r.status, '')}"
        for r in rows
    ]


def _clip(s: str) -> str:
    return s if len(s) <= _MSG_MAX else s[: _MSG_MAX - 1].rstrip() + "…"


def _send(chat_id: str, message: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
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
    ap = argparse.ArgumentParser(description="Send the daily Telegram prep + order brief.")
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    ap.add_argument("--date", help="prep target date YYYY-MM-DD (default: tomorrow IST)")
    ap.add_argument(
        "--only", choices=["both", "order"], default="both",
        help="'order' sends just the reorder list (evening run); default 'both' "
             "sends prep + order.",
    )
    args = ap.parse_args()

    # Windows consoles default to cp1252; the message uses "•" and "—". Never
    # let a console encoding crash the job (Render is already UTF-8; no-op there).
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
        order_lines = _order_lines(db)
        order_block = "\n".join(order_lines) if order_lines else "nothing due"

        if args.only == "order":
            date_label = today.strftime("%a %d %b")
            message = _clip(
                f"<b>URS Majestic — Order Forecast ({html.escape(date_label)})</b>\n\n"
                f"<b>To order now:</b>\n{order_block}"
            )
        else:
            date_label = target.strftime("%a %d %b")
            prep_lines = _prep_lines(db, target)
            prep_block = "\n".join(prep_lines) if prep_lines else "nothing flagged"
            message = _clip(
                f"<b>URS Majestic — Daily Brief ({html.escape(date_label)})</b>\n\n"
                f"<b>Prep for {html.escape(date_label)}:</b>\n{prep_block}\n\n"
                f"<b>To order now:</b>\n{order_block}"
            )
    finally:
        db.close()

    chat_ids = [c.strip() for c in settings.TELEGRAM_CHAT_IDS.split(",") if c.strip()]

    print(f"== Telegram {'order forecast' if args.only == 'order' else 'daily brief'} ==========")
    print(message)
    print(f"\nRecipients : {', '.join(chat_ids) or '(none configured)'}")

    configured = bool(settings.TELEGRAM_BOT_TOKEN)
    if args.dry_run or not configured:
        why = "--dry-run" if args.dry_run else "TELEGRAM_BOT_TOKEN not set"
        print(f"\n[{why}] nothing sent.")
        return 0

    if not chat_ids:
        print("\nNo recipients configured (TELEGRAM_CHAT_IDS). Nothing sent.")
        return 1

    failures = 0
    for chat_id in chat_ids:
        ok, resp = _send(chat_id, message)
        print(f"  -> {chat_id}: {'SENT' if ok else 'FAILED'} - {resp[:200]}")
        failures += 0 if ok else 1
    print(f"\nDone: {len(chat_ids) - failures}/{len(chat_ids)} sent.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
