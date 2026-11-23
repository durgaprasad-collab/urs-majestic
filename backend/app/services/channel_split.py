"""Splitting a day's Petpooja sales into walk-in and the platforms that feed Petpooja.

Zomato, Swiggy and Ownly orders now arrive in Petpooja, so Petpooja's Item Report total for a day
includes them. The Order Listing tags each of those orders (petpooja_order_times.source), so for each
day this works out:
  * the 'petpooja' (walk-in) row  = item report total - the tagged platform orders' amounts
  * the 'ownly' row               = Ownly's tagged orders (no other feed exists for Ownly)
Zomato and Swiggy keep their own rows from their portals' files, which are the real figures for those
channels. Safe to run any number of times and in either order (item report first or order listing first):
it recomputes from what is stored."""
import decimal

from sqlalchemy import text

D = decimal.Decimal


def recompute_split(db, dates) -> None:
    for d in sorted(set(dates)):
        tagged = {r["source"]: r for r in db.execute(text("""
            SELECT source, count(*) n, sum(amount) amount, sum(discount) discount
              FROM petpooja_order_times WHERE business_date = :d AND source IS NOT NULL GROUP BY source"""),
            {"d": d}).mappings()}
        if not tagged:
            continue
        item_total = db.execute(text("SELECT sum(revenue) FROM item_sales WHERE sale_date = :d"), {"d": d}).scalar()
        if item_total is not None:
            platform_amount = sum((D(str(r["amount"])) for r in tagged.values()), D("0"))
            db.execute(text("""
                UPDATE daily_channel_sales SET net_sales = greatest(0, :n)
                 WHERE business_date = :d AND channel = 'petpooja'"""),
                {"d": d, "n": D(str(item_total)) - platform_amount})
        # Since add-on 1.7.0 Zomato's and Swiggy's portals are no longer read, so nothing else writes their
        # rows. Fill them from the tagged orders (amount less discount); a row that came from a portal file
        # (any other source_file) is left alone.
        for ch in ("zomato", "swiggy"):
            t = tagged.get(ch)
            if not t:
                continue
            db.execute(text("""
                INSERT INTO daily_channel_sales (business_date, channel, net_sales, orders, restaurant_discount,
                                                 platform_discount, source_file)
                VALUES (:d, :c, greatest(0, :a - :x), :n, :x, 0, 'petpooja order listing')
                ON CONFLICT (business_date, channel) DO UPDATE
                   SET net_sales = EXCLUDED.net_sales, orders = EXCLUDED.orders,
                       restaurant_discount = EXCLUDED.restaurant_discount, source_file = EXCLUDED.source_file,
                       uploaded_at = now()
                 WHERE daily_channel_sales.source_file LIKE 'petpooja order listing%'
                    OR daily_channel_sales.source_file LIKE 'manual %'"""),
                {"d": d, "c": ch, "a": t["amount"], "n": t["n"], "x": t["discount"] or 0})
        own = tagged.get("ownly")
        if own:
            db.execute(text("""
                INSERT INTO daily_channel_sales (business_date, channel, net_sales, orders, restaurant_discount,
                                                 platform_discount, source_file)
                VALUES (:d, 'ownly', :a, :n, :x, 0, 'petpooja order listing')
                ON CONFLICT (business_date, channel) DO UPDATE
                   SET net_sales = EXCLUDED.net_sales, orders = EXCLUDED.orders,
                       restaurant_discount = EXCLUDED.restaurant_discount, source_file = EXCLUDED.source_file,
                       uploaded_at = now()"""),
                {"d": d, "a": own["amount"], "n": own["n"], "x": own["discount"]})
        else:
            db.execute(text("DELETE FROM daily_channel_sales WHERE business_date = :d AND channel = 'ownly' "
                            "AND source_file = 'petpooja order listing'"), {"d": d})
