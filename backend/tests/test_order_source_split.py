import datetime
import decimal
import unittest

from app.services import order_listing_ingest as oli
from app.services.uploads import petpooja_order_listing as pol


class SourceTests(unittest.TestCase):
    def test_source_of(self):
        self.assertEqual(pol.source_of("Delivery (Zomato)"), "zomato")
        self.assertEqual(pol.source_of("Delivery (Swiggy)"), "swiggy")
        self.assertEqual(pol.source_of("Delivery (Ownly)"), "ownly")
        self.assertIsNone(pol.source_of("Pick Up (Pick Up)"))
        self.assertIsNone(pol.source_of("Dine In"))
        self.assertIsNone(pol.source_of(None))

    def feed(self):
        def row(no, typ, amt, created, status="Printed"):
            return {"no": no, "type": typ, "amount": amt, "discount": "0", "status": status, "created": created}
        return [row("1", "Delivery (Zomato)", "169.00", "2026-11-19 13:22:01"),
                row("2", "Delivery (Swiggy)", "519.00", "2026-11-19 13:04:06"),
                row("3", "Delivery (Ownly)", "200.00", "2026-11-19 14:00:00"),
                row("4", "Pick Up (Pick Up)", "129.00", "2026-11-19 13:57:54"),
                row("5", "Delivery (Zomato)", "99.00", "2026-11-19 15:00:00", "Cancelled")]

    def test_feed_keeps_source_and_walkin_count_excludes_platforms(self):
        rows, _ = oli.rows_from_feed("2026-11-19", self.feed())
        self.assertEqual([r.source for r in rows], ["zomato", "swiggy", "ownly", None, "zomato"])
        daily = pol.aggregate_by_day(rows, datetime.date(2026, 11, 25))
        self.assertEqual(daily[datetime.date(2026, 11, 19)], {"count": 1, "amount": decimal.Decimal("129.00")})

    def test_tagged_delivery_is_not_a_double_count_alarm(self):
        rows, _ = oli.rows_from_feed("2026-11-19", self.feed())
        self.assertFalse(pol.check_aggregator_alarm(rows))
        rows[3].order_type, rows[3].source = "Delivery", None   # an untagged delivery still is
        self.assertTrue(pol.check_aggregator_alarm(rows))
