"""Align delivery-channel daily_channel_sales (net_sales, orders) to the orders
table aggregated by IST business date.

daily_channel_sales for Zomato/Swiggy is derived from delivered orders at upload
time; rows written before the IST-dating fix can disagree with the (now IST)
reconciliation crosscheck by a day at the midnight boundary, showing up as
+X / -X mismatches on adjacent days. Re-deriving net/orders from the orders
table makes daily match the crosscheck exactly. Idempotent — a no-op for rows
already correct.

Run from backend/:  D:\\URS_Majestic\\.venv\\Scripts\\python.exe -m scripts.align_channel_daily
"""
from app.core.database import SessionLocal
from sqlalchemy import text


def main() -> None:
    db = SessionLocal()
    try:
        result = db.execute(text("""
            WITH agg AS (
                SELECT channel,
                       (placed_at AT TIME ZONE 'Asia/Kolkata')::date AS bd,
                       round(sum(total_amount), 2) AS net,
                       count(*) AS cnt
                FROM orders
                WHERE status = 'delivered' AND channel IN ('zomato', 'swiggy')
                GROUP BY channel, (placed_at AT TIME ZONE 'Asia/Kolkata')::date
            )
            UPDATE daily_channel_sales d
            SET net_sales = a.net, orders = a.cnt
            FROM agg a
            WHERE d.channel = a.channel AND d.business_date = a.bd
              AND (d.net_sales <> a.net OR COALESCE(d.orders, -1) <> a.cnt)
        """))
        db.commit()
        print(f"aligned {result.rowcount} delivery daily row(s) to the IST orders crosscheck")
    finally:
        db.close()


if __name__ == "__main__":
    main()
