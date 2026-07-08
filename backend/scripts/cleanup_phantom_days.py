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
        if phantom:
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
        else:
            print("no phantom days found")

        # Second pass: trailing per-channel zero rows — a channel row with no
        # sales/orders dated AFTER that channel's last real sale. These are
        # leftover zero-fill (pre-clamp) or stale from an export whose range ran
        # ahead of its data; they make a lagging channel read a misleading "₹0"
        # on the latest day instead of the honest "not reported yet".
        trailing = db.execute(text("""
            SELECT business_date, channel FROM daily_channel_sales d
            WHERE d.net_sales = 0 AND COALESCE(d.orders, 0) = 0
              AND d.business_date > (
                SELECT COALESCE(max(d2.business_date), DATE '1900-01-01')
                FROM daily_channel_sales d2
                WHERE d2.channel = d.channel AND d2.net_sales > 0
              )
            ORDER BY channel, business_date
        """)).all()
        if trailing:
            print("removing trailing per-channel zero rows:", [(str(d), ch) for d, ch in trailing])
            db.execute(text("""
                DELETE FROM daily_channel_sales d
                WHERE d.net_sales = 0 AND COALESCE(d.orders, 0) = 0
                  AND d.business_date > (
                    SELECT COALESCE(max(d2.business_date), DATE '1900-01-01')
                    FROM daily_channel_sales d2
                    WHERE d2.channel = d.channel AND d2.net_sales > 0
                  )
            """))
            db.commit()
            print(f"removed {len(trailing)} trailing zero row(s)")
        else:
            print("no trailing per-channel zero rows")
    finally:
        db.close()


if __name__ == "__main__":
    main()
