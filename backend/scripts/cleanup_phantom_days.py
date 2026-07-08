"""One-off cleanup: remove 'phantom' business days from daily_channel_sales —
dates whose ONLY rows are zero-fill (no channel has any net_sales or orders).

These were created before the zero-fill clamp landed: a channel export whose
declared range ran past the last real order wrote a trailing ₹0 row, which
advanced the dashboard's "latest day" onto a day with no sales. Deleting them is
safe (they carry no data) and idempotent.

Run from backend/:  D:\\URS_Majestic\\.venv\\Scripts\\python.exe -m scripts.cleanup_phantom_days
"""
from app.core.database import SessionLocal
from sqlalchemy import text


def main() -> None:
    db = SessionLocal()
    try:
        phantom = db.execute(text("""
            SELECT business_date FROM daily_channel_sales
            GROUP BY business_date
            HAVING COALESCE(SUM(net_sales), 0) = 0 AND COALESCE(SUM(orders), 0) = 0
            ORDER BY business_date
        """)).scalars().all()
        if not phantom:
            print("no phantom days found")
            return
        print("deleting phantom days:", [str(d) for d in phantom])
        db.execute(
            text("""
                DELETE FROM daily_channel_sales
                WHERE business_date = ANY(:dates)
                  AND business_date IN (
                    SELECT business_date FROM daily_channel_sales
                    GROUP BY business_date
                    HAVING COALESCE(SUM(net_sales), 0) = 0 AND COALESCE(SUM(orders), 0) = 0
                  )
            """),
            {"dates": phantom},
        )
        db.commit()
        print(f"removed rows on {len(phantom)} phantom day(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
