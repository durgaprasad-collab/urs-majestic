"""Daily task engine: turns the day's data into the top tasks for each role.

Runs after every sales upload (web uploads and scripts.import_pos). It reads
the same numbers as the eagle-eye strip (shell_pulse) plus a few operational
signals, and writes tasks to `daily_tasks` for the data's business date.

Roles (the five delegated seats on the Today page):
  gm        Restaurant GM      - floor, counter, stock on hand, staff
  coo       COO                - menu, pricing, costs, platform listings
  cro       CRO / BizDev       - revenue growth, channels, offers, catering
  creative  Creative Director  - photos, social, reviews, promotion collateral
  bi        BI Manager         - uploads, data quality, settings, reporting

Every rule is a plain function of the data with its evidence written into the
task, so the owner can see why a task exists. Rules that don't fire produce no
task; re-running a day refreshes open tasks and keeps the ones marked done.
"""
import datetime
import logging
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.database import SessionLocal
from app.services import shell_pulse

log = logging.getLogger(__name__)

ROLE_LABELS = {
    "gm": "Restaurant GM",
    "coo": "COO",
    "cro": "CRO / BizDev",
    "creative": "Creative Director",
    "bi": "BI Manager",
}
PRIORITY_WEIGHT = {"P0": 300, "P1": 200, "P2": 100}


def _inr(x) -> str:
    return f"₹{float(x or 0):,.0f}"


class _Tasks:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, key, role, priority, title, detail, done_means=None, href=None, impact=0.0):
        # Score orders tasks: priority band first, then rupee impact within it.
        self.items.append({
            "rule_key": key, "role": role, "priority": priority, "title": title, "detail": detail,
            "done_means": done_means, "href": href,
            "score": PRIORITY_WEIGHT[priority] + min(float(impact or 0) / 1000, 99),
        })


def build_tasks(db: Session) -> tuple[datetime.date | None, list[dict]]:
    """Evaluate every rule against current data. Returns (business_date, tasks)."""
    p = shell_pulse._build(db)
    if not p.get("has_data"):
        return None, []
    asof: datetime.date = p["asof"]
    today = business_today()
    s, prof, ch, lk, go, tr, b = p["sales"], p["profit"], p["channels"], p["leaks"], p["gas_oil"], p["trust"], p["badges"]
    t = _Tasks()

    # ── Sales vs break-even ──────────────────────────────────────────────
    wk = db.execute(text("""
        SELECT business_date d, extract(isodow FROM business_date) dow, sum(net_sales) tot,
               sum(net_sales) FILTER (WHERE channel = 'petpooja') walk
        FROM daily_channel_sales
        WHERE business_date > :asof - 28 AND business_date <= :asof
        GROUP BY 1, 2 ORDER BY 1
    """), {"asof": asof}).mappings().all()
    be = s["be"]
    weekday = [r for r in wk if r["dow"] <= 4]
    weekday_ok = sum(1 for r in weekday if float(r["tot"]) >= be)
    weekend = [r for r in wk if r["dow"] >= 5]
    weekend_ok = sum(1 for r in weekend if float(r["tot"]) >= be)
    if weekday and weekday_ok <= len(weekday) / 3:
        avg_wd = sum(float(r["tot"]) for r in weekday) / len(weekday)
        gap = be - avg_wd
        t.add("weekday_offer", "cro", "P0",
              "Lift Mon–Thu sales: launch a weekday dinner offer",
              f"Only {weekday_ok} of the last {len(weekday)} Mon–Thu days cleared break-even ({_inr(be)}), "
              f"vs {weekend_ok} of {len(weekend)} Fri–Sun days. Weekdays average {_inr(avg_wd)}, {_inr(gap)} short.",
              "An offer is live on the counter, Zomato and Swiggy for Mon–Thu dinner, and its sales are tracked.",
              "/results", impact=gap * 17)
        t.add("weekday_promo_creative", "creative", "P1",
              "Promote the weekday offer: counter board, WhatsApp status, Instagram",
              f"Weekday sales are the gap ({_inr(gap)}/day under break-even). The offer needs to be seen to work.",
              "Offer artwork is on the counter and posted on WhatsApp status and Instagram.", impact=gap * 8)
    elif s["vs_be"] < 0:
        t.add("below_be", "cro", "P1",
              f"Yesterday finished {_inr(-s['vs_be'])} below break-even",
              f"{asof:%a %d %b}: {_inr(s['total'])} vs break-even {_inr(be)}.",
              "Know which channel or daypart caused the miss and what changes today.", "/daily-brief",
              impact=-s["vs_be"])

    # Month goal
    if prof["value"] is not None and prof["need_per_day"] and prof["need_per_day"] > prof["daily_avg"] * 1.15:
        t.add("month_goal", "bi", "P1",
              f"{asof:%B} profit goal at risk: need {_inr(prof['need_per_day'])}/day",
              f"Pace is {_inr(prof['daily_avg'])}/day (projected profit {_inr(prof['value'])} vs goal {_inr(prof['goal'])}). "
              f"Achieved so far {_inr(prof['achieved'])}; {_inr(prof['to_go'])} to go in {prof['remaining_days']} days.",
              "A short note to the owner on which levers close the gap, reviewed on Monday.", "/business-settings",
              impact=prof["to_go"] / 10)

    # Walk-in trend: last 7 days vs the 7 before
    last7 = [r for r in wk if (asof - r["d"]).days < 7]
    prev7 = [r for r in wk if 7 <= (asof - r["d"]).days < 14]
    if last7 and prev7:
        w1 = sum(float(r["walk"] or 0) for r in last7)
        w0 = sum(float(r["walk"] or 0) for r in prev7)
        if w0 and (w1 / w0 - 1) <= -0.10:
            t.add("walkin_drop", "gm", "P1",
                  f"Walk-in sales down {abs(w1 / w0 - 1) * 100:.0f}% week on week",
                  f"Counter (Petpooja) sales: {_inr(w1)} in the last 7 days vs {_inr(w0)} the week before, "
                  f"while Zomato is growing. Check footfall, counter service and staffing at dinner.",
                  "Cause noted (footfall, service, prices or staffing) and one fix in place.", impact=w0 - w1)

    # Weekend prep: tomorrow (relative to today) is Saturday or Sunday
    tomorrow = today + datetime.timedelta(days=1)
    if tomorrow.isoweekday() >= 6 and weekend:
        same = [float(r["tot"]) for r in weekend if r["dow"] == tomorrow.isoweekday()]
        if same:
            t.add(f"weekend_prep_{tomorrow:%a}".lower(), "gm", "P1",
                  f"Prep for {tomorrow:%A}: expect about {_inr(sum(same) / len(same))}",
                  f"{tomorrow:%A}s averaged {_inr(sum(same) / len(same))} over the last 4 weeks, "
                  f"well above weekdays. Stock and staff for the rush.",
                  "Prep sheet, stock and staff roster set for the weekend volume.", "/prep-sheet")

    # ── Channels ─────────────────────────────────────────────────────────
    if ch["zomato_disc_pct"] >= 15:
        t.add("zomato_discount", "cro", "P1",
              f"Review Zomato discounts: you fund {ch['zomato_disc_pct']}% of order value",
              f"{asof:%B} to date: Zomato {_inr(ch['zomato'])} net from {ch['zomato_orders']} orders. "
              f"Restaurant-funded discount is {ch['zomato_disc_pct']}% of gross on top of commission.",
              "Each running Zomato offer is kept, cut or replaced, with the reason written down.",
              impact=ch["zomato"] * ch["zomato_disc_pct"] / 100)
    sw = db.execute(text("""
        SELECT coalesce(sum(orders), 0) FROM daily_channel_sales
        WHERE channel = 'swiggy' AND business_date > :asof - 30
    """), {"asof": asof}).scalar() or 0
    if sw < 30:
        t.add("swiggy_weak", "cro", "P1",
              f"Swiggy is barely selling: {sw} orders in 30 days",
              f"Zomato did {ch['zomato_orders']} orders this month alone. Check Swiggy listing status, "
              f"photos, prices and serviceable area.",
              "Swiggy listing checked end to end and one visibility fix made.")
    late = db.execute(text("""
        SELECT count(*) FILTER (WHERE extract(hour FROM placed_at AT TIME ZONE 'Asia/Kolkata') IN (23, 0)) late,
               count(*) total
        FROM orders WHERE channel = 'zomato' AND placed_at > now() - interval '30 days'
    """)).mappings().one()
    if late["total"] >= 40 and late["late"] / late["total"] >= 0.25:
        t.add("late_night", "coo", "P2",
              f"Plan for the 11 PM–1 AM Zomato rush ({late['late'] / late['total'] * 100:.0f}% of orders)",
              f"{late['late']} of {late['total']} Zomato orders in 30 days came between 11 PM and 1 AM.",
              "Late-shift staffing and prep cover the last two hours.")

    # ── Menu & costs ─────────────────────────────────────────────────────
    for i, r in enumerate(lk["rows"][:2]):
        t.add(f"leak_{r['name']}", "coo", "P1" if i == 0 else "P2",
              f"Fix the margin on {r['name']}: {r['pct']}% food cost",
              f"Sells at {_inr(r['price'])}, {r['qty']} sold in 30 days. Cost above a 35% target is "
              f"{_inr(r['leak'])}/month{' — and it is a Zomato bestseller' if r['zomato_hit'] else ''}.",
              "Portion, recipe or price changed and the new food cost recorded.", "/results", impact=r["leak"])
    dead = db.execute(text("""
        SELECT m.name FROM menu_items m
        WHERE m.is_active AND m.is_food AND NOT EXISTS (
            SELECT 1 FROM item_sales s WHERE s.item_name = m.name AND s.sale_date > :asof - 30)
        ORDER BY m.name
    """), {"asof": asof}).scalars().all()
    if dead:
        t.add("dead_items", "coo", "P2",
              f"{len(dead)} dishes sold nothing in 30 days",
              f"{', '.join(dead[:6])}{' …' if len(dead) > 6 else ''}. Drop, rework or promote them.",
              "Each dish is kept with a reason, reworked, or removed from all channels.", "/results")
    if go["pct"] >= 12:
        t.add("gas_oil", "coo", "P2",
              f"Gas + oil are {go['pct']}% of sales",
              f"{go['month']:%B}: gas {_inr(go['gas'])} ({go['gas_pct']}%), oil {_inr(go['oil'])} ({go['oil_pct']}%).",
              "One saving tried (burner use, batch frying, oil reuse policy) and measured.", "/purchases",
              impact=go["gas"] / 10)

    # ── Stock & floor ────────────────────────────────────────────────────
    runout = db.execute(text("""
        SELECT name, on_hand_qty, unit, stock_days_cover_left FROM v_ingredient_reorder_forecast
        WHERE is_active AND on_hand_qty >= 0 AND stock_days_cover_left IS NOT NULL AND stock_days_cover_left < 1
        ORDER BY stock_days_cover_left
    """)).mappings().all()
    if runout:
        names = ", ".join(r["name"] for r in runout[:6])
        t.add("runout_today", "gm", "P0",
              f"Buy today: {names}",
              f"{len(runout)} ingredient(s) have less than a day of stock left at the latest count.",
              "Bought and logged in Purchases before service.", "/order-forecast", impact=5000)
    if b["requisitions"]:
        t.add("requisitions", "gm", "P0",
              f"Approve or reject {b['requisitions']} staff requisition(s)",
              "Staff are waiting on these to buy or prepare.", "Every pending requisition decided.", "/requisitions",
              impact=3000)
    if go["gas_age"] is not None and go["gas_age"] > 4:
        t.add("gas_bills", "gm", "P1",
              f"Enter cylinder bills: none logged for {go['gas_age']} days",
              "Gas is the largest purchase line; missing bills overstate profit.",
              "All cylinders bought since the last entry are in Purchases.", "/purchases/new")

    # ── Data & reporting (BI) ────────────────────────────────────────────
    lag = (today - asof).days
    if lag > 1:
        t.add("upload_late", "bi", "P0",
              f"Sales data is {lag} days old",
              f"Latest business day loaded is {asof:%a %d %b}.", "Petpooja report for yesterday uploaded.", "/upload",
              impact=9000)
    for i in tr["issues"]:
        if i["key"] == "stock":
            t.add("neg_stock", "bi", "P1", f"Fix {i['value']} stock lines",
                  "Ingredients below zero break the stock and reorder numbers — usually a g/kg unit mix-up in a recipe.",
                  "No ingredient shows negative stock.", i["href"])
        elif i["key"] == "swiggy":
            t.add("swiggy_upload", "bi", "P0", f"Upload the Swiggy report ({i['value']})", i["note"] + ".",
                  "Swiggy data is current to yesterday.", i["href"], impact=4000)
        elif i["key"] == "settings":
            t.add("margin_setting", "bi", "P1", f"Review the contribution margin ({i['value']})",
                  "Every target and break-even on this page is computed from it. Re-derive it from last month's "
                  "purchases vs sales.", "Margin re-set in Business Settings with a note on how it was derived.",
                  i["href"])
        elif i["key"] == "orders":
            t.add("order_counts", "bi", "P2", f"Petpooja order counts: {i['value']}", i["note"] + ".",
                  "Bad days corrected or explained.", i["href"])
        elif i["key"] == "prices":
            t.add("menu_prices", "bi", "P1", f"Menu prices: {i['value']}", i["note"] + ".",
                  "Menu table matches what Petpooja bills.", i["href"])
        elif i["key"] == "recon":
            t.add("recon", "bi", "P0", f"Reconcile {i['value']}", i["note"] + ".", "No unexplained mismatch.",
                  i["href"], impact=6000)
    if b["purchases"]:
        t.add("ledger_issues", "bi", "P2", f"Resolve {b['purchases']} purchase-ledger issue(s)",
              "Open sync issues between purchases and the cost ledger.", "Issue list empty.", "/purchases")

    # ── Brand & promotion (Creative) ─────────────────────────────────────
    rv = db.execute(text("""
        SELECT value, effective_from FROM business_settings WHERE setting_key = 'google_review_count'
        ORDER BY effective_from DESC, id DESC LIMIT 1
    """)).mappings().first()
    if rv is None or float(rv["value"]) < 50 or (today - rv["effective_from"]).days > 30:
        cnt = int(rv["value"]) if rv else 0
        when = f", last updated {rv['effective_from']:%d %b}" if rv else ""
        t.add("google_reviews", "creative", "P1",
              f"Grow Google reviews: {cnt} on record{when}",
              "Reviews drive walk-in discovery. Ask happy tables at the counter with a QR card, and update the count weekly.",
              "Review QR on every table/counter and the count updated this week.", "/daily-brief")
    fb = db.execute(text("""
        SELECT count(*) FROM customer_feedback WHERE NOT coalesce(is_test, false) AND created_at > now() - interval '30 days'
    """)).scalar() or 0
    if fb == 0:
        t.add("feedback", "creative", "P2", "No customer feedback collected in 30 days",
              "The QR feedback page exists but has no real responses.", "Feedback QR placed and first responses in.")
    top = db.execute(text("""
        SELECT s.item_name, sum(s.qty) q FROM item_sales s JOIN menu_items m ON m.name = s.item_name
        WHERE m.is_food AND s.sale_date > :asof - 7 GROUP BY 1 ORDER BY 2 DESC LIMIT 1
    """), {"asof": asof}).mappings().first()
    if top:
        t.add("feature_bestseller", "creative", "P2",
              f"Feature the bestseller: {top['item_name']} ({int(top['q'])} sold this week)",
              "Lead with what already sells: hero photo on Zomato/Swiggy, counter board and social posts.",
              "Bestseller photo is first on both apps and posted this week.")

    # ── Revenue development (CRO) ────────────────────────────────────────
    cat = db.execute(text("SELECT max(order_taken_date) FROM catering_orders")).scalar()
    if cat is None or (today - cat).days > 21:
        t.add("catering", "cro", "P2",
              f"Find the next catering order (last one {cat:%d %b})" if cat else "Start catering sales",
              "Catering orders are large tickets (₹5,500–₹8,000 so far). Pitch nearby offices, temples and events.",
              "Three catering pitches made this week.", "/catering-orders")

    return asof, t.items


def generate_daily_tasks(db: Session) -> dict:
    """Rebuild the open tasks for the data's latest business date.

    Done tasks for that date are kept (not resurrected); everything else for
    the date is replaced. Commits. Never raises to the caller's upload flow."""
    try:
        asof, tasks = build_tasks(db)
        if asof is None:
            return {"business_date": None, "generated": 0}
        done = set(db.execute(text(
            "SELECT rule_key FROM daily_tasks WHERE business_date = :d AND status = 'done'"), {"d": asof}).scalars())
        db.execute(text("DELETE FROM daily_tasks WHERE business_date = :d AND status = 'open'"), {"d": asof})
        fresh = [x for x in tasks if x["rule_key"] not in done]
        for x in fresh:
            db.execute(text("""
                INSERT INTO daily_tasks (business_date, rule_key, role, priority, score, title, detail, done_means, href)
                VALUES (:d, :rule_key, :role, :priority, :score, :title, :detail, :done_means, :href)
            """), {"d": asof, **x})
        db.commit()
        log.info("daily tasks for %s: %d generated, %d already done", asof, len(fresh), len(done))
        return {"business_date": asof, "generated": len(fresh), "kept_done": len(done)}
    except Exception:
        log.exception("daily task generation failed")
        db.rollback()
        return {"business_date": None, "generated": 0, "error": True}


def get_tasks(db: Session, business_date: datetime.date | None = None) -> dict:
    """Tasks for a business date (default: latest generated), highest score first."""
    d = business_date or db.execute(text("SELECT max(business_date) FROM daily_tasks")).scalar()
    if d is None:
        return {"business_date": None, "open": [], "done": [], "generated_at": None}
    rows = db.execute(text("""
        SELECT id, rule_key, role, priority, title, detail, done_means, href, status, done_by, done_at, generated_at
        FROM daily_tasks WHERE business_date = :d ORDER BY score DESC, id
    """), {"d": d}).mappings().all()
    items = [dict(r, role_label=ROLE_LABELS[r["role"]]) for r in rows]
    return {
        "business_date": d,
        "open": [r for r in items if r["status"] == "open"],
        "done": [r for r in items if r["status"] == "done"],
        "generated_at": max((r["generated_at"] for r in items), default=None),
    }


def mark_done(db: Session, task_id: int, done_by: str) -> bool:
    n = db.execute(text("""
        UPDATE daily_tasks SET status = 'done', done_at = now(), done_by = :by
        WHERE id = :id AND status = 'open'
    """), {"id": task_id, "by": done_by}).rowcount
    db.commit()
    return bool(n)


def refresh_after_upload(background: bool = True) -> None:
    """Call after a sales upload commits: regenerate today's tasks and refresh
    the eagle-eye strip so both reflect the new data straight away. Web
    uploads run it in a background thread so the upload response isn't held
    up; the CLI import runs it inline."""
    def run():
        db = SessionLocal()
        try:
            generate_daily_tasks(db)
        finally:
            db.close()
        shell_pulse.invalidate()
        shell_pulse.get_shell_context()

    if background:
        threading.Thread(target=run, name="daily-tasks", daemon=True).start()
    else:
        run()
