import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.services import swiggy_ingest


class FakeIndex:
    def match(self, name):
        return None if name == "Mystery" else 7


def order(**kw):
    base = {"id": "251652135103892", "status": "delivered", "placed": "2026-10-22T20:52:15", "tax": [16.48, 16.48, 0],
            "trade": 0, "offers": 0, "items": [{"name": "Paneer Butter Masala", "qty": 1, "total": 269}, {"name": "Butter Naan (1 No)", "qty": 5, "total": 435}]}
    return {**base, **kw}


class OrdersFromFeedTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(swiggy_ingest, "business_today", return_value=date(2026, 10, 30))
        p.start()
        self.addCleanup(p.stop)

    def test_matches_what_the_csv_upload_stored_for_the_same_order(self):
        # 251652135103892 as uploaded from Swiggy's CSV: net 659.20, items 269.00 and 87.00 each
        (o,), unmapped = swiggy_ingest.orders_from_feed("2026-10-22", [order()], FakeIndex())
        self.assertEqual((o.status, o.net, o.gross, o.restaurant_discount), ("delivered", Decimal("659.20"), Decimal("659.20"), Decimal("0.00")))
        self.assertEqual([(i.raw_name, i.quantity, i.unit_price) for i in o.items],
                         [("Paneer Butter Masala", 1, Decimal("269.00")), ("Butter Naan (1 No)", 5, Decimal("87.00"))])
        self.assertEqual(unmapped, set())

    def test_anything_not_delivered_is_cancelled_and_discounts_add_to_gross(self):
        (o,), _ = swiggy_ingest.orders_from_feed("2026-10-22", [order(status="cancelled", trade=10, offers="5.5")], FakeIndex())
        self.assertEqual((o.status, o.restaurant_discount, o.gross), ("cancelled", Decimal("15.50"), Decimal("674.70")))

    def test_unmatched_names_are_reported_and_other_days_skipped(self):
        orders, unmapped = swiggy_ingest.orders_from_feed(
            "2026-10-22", [order(items=[{"name": "Mystery", "qty": 1, "total": 100}]), order(id="2", placed="2026-10-23T00:10:00")], FakeIndex())
        self.assertEqual(len(orders), 1)
        self.assertEqual(unmapped, {"Mystery"})

    def test_refuses_unfinished_days_and_bad_rows(self):
        with self.assertRaisesRegex(ValueError, "not finished"):
            swiggy_ingest.orders_from_feed("2026-10-30", [order(placed="2026-10-30T12:00:00")], FakeIndex())
        for bad in (order(id="abc"), order(tax=[1, 2]), order(placed="soon"), order(tax=[-1, 0, 0])):
            with self.assertRaises(ValueError):
                swiggy_ingest.orders_from_feed("2026-10-22", [bad], FakeIndex())


if __name__ == "__main__":
    unittest.main()
