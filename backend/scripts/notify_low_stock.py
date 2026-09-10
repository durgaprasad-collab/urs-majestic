"""Push a reminder to staff for every ingredient under 3 days of cover.

Run on a schedule (same mechanism as send_daily_whatsapp.py / cron):
    python scripts/notify_low_stock.py

Sends one push per active staff (non-admin) user listing what needs a count,
via the same FCM helper the app's requisition/decision notifications use.
Silently does nothing if there's nothing low, or if FCM isn't configured yet
(logs a dry-run line instead -- see app/services/push_notifications.py).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.models.user import User
from app.services import push_notifications
from app.web.stock_log_routes import _stock_rows


def main() -> None:
    db = SessionLocal()
    try:
        rows = _stock_rows(db)
        low = [r for r in rows if r["cover_colour"] == "red" and r["name"] != "Cooking Gas"]
        if not low:
            print("Nothing under 3 days of cover -- no notification sent.")
            return

        staff_ids = [
            uid for (uid,) in db.query(User.id).filter(User.is_admin.is_(False), User.is_active.is_(True)).all()
        ]
        if not staff_ids:
            print("No active staff accounts to notify.")
            return

        names = ", ".join(r["name"] for r in low[:8])
        if len(low) > 8:
            names += f" +{len(low) - 8} more"
        push_notifications.send_to_users(
            db, staff_ids,
            title=f"{len(low)} item(s) need a stock count",
            body=names,
        )
        print(f"Notified {len(staff_ids)} staff about {len(low)} low-cover item(s): {names}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
