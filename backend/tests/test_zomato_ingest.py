import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.services import zomato_ingest


class FakeIndex:
    def match(self, name):
        return None if name == "Mystery" else 3


def order(**kw):
    base = {"id": "8791492682", "state": "DELIVERED", "created": "2026-10-31T07:26:13Z", "subtotal": 1027,
            "salt": 103.6, "pct": 103.6, "abs": 87.75, "items": [{"name": "Butter Naan (1 No)", "qty": 2}]}
    return {**base, **kw}


class OrdersFromFeedTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(zomato_ingest, "business_today", return_value=date(2026, 11, 2))
        p.start()
        self.addCleanup(p.stop)

    def one(self, **kw):
        (o,), _ = zomato_ingest.orders_from_feed("2026-10-31", [order(**kw)], FakeIndex())
        return o

    def test_nets_match_what_the_csv_upload_stored_for_the_same_orders(self):
        # Each case is an order already uploaded from Zomato's CSV, with its stored net amount.
        self.assertEqual(self.one().net, Decimal("835.65"))                                          # 8791492682
        self.assertEqual(self.one(id="8764900458", subtotal=509, salt=18.4, pct=0, abs=2).net, Decimal("488.60"))
        self.assertEqual(self.one(id="8783625744", subtotal=195, salt=0, pct=46.15, abs=0).net, Decimal("148.85"))  # no per-dish %, so the order-level one
        self.assertEqual(self.one(id="8767682878", subtotal=365, salt=73, pct=175.2, abs=0).net, Decimal("292.00"))  # per-dish % wins over a bigger order-level figure

    def test_gross_status_time_and_items(self):
        o = self.one()
        self.assertEqual((o.gross, o.restaurant_discount, o.platform_discount, o.status),
                         (Decimal("1027.00"), Decimal("191.35"), Decimal("0"), "delivered"))
        self.assertEqual((o.placed_at.hour, o.placed_at.minute), (12, 56))   # 07:26 UTC is 12:56 in India
        self.assertEqual([(i.raw_name, i.quantity, i.unit_price) for i in o.items], [("Butter Naan (1 No)", 2, None)])
        for state in ("MERCHANT_REJECTED", "USER_CANCELLED", "READY"):
            self.assertEqual(self.one(state=state).status, "cancelled")

    def test_other_days_skipped_unmatched_reported_and_bad_input_refused(self):
        orders, unmapped = zomato_ingest.orders_from_feed(
            "2026-10-31", [order(items=[{"name": "Mystery", "qty": 1}]), order(id="2", created="2026-10-31T19:00:00Z")], FakeIndex())
        self.assertEqual(len(orders), 1)    # 19:00 UTC is already 1 November in India
        self.assertEqual(unmapped, {"Mystery"})
        with self.assertRaisesRegex(ValueError, "not finished"):
            zomato_ingest.orders_from_feed("2026-11-02", [order()], FakeIndex())
        for bad in (order(id="x"), order(created="soon"), order(created="2026-10-31T07:26:13"), order(subtotal=-1)):
            with self.assertRaises(ValueError):
                zomato_ingest.orders_from_feed("2026-10-31", [bad], FakeIndex())


if __name__ == "__main__":
    unittest.main()
