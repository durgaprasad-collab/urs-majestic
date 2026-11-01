import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.services import order_listing_ingest


def row(**kw):
    base = {"no": "77", "type": "Pick Up(Pick Up)", "amount": "713.00", "discount": "(0.00)", "status": "Printed", "created": "2026-11-01 00:50:05"}
    return {**base, **kw}


class RowsFromFeedTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(order_listing_ingest, "business_today", return_value=date(2026, 11, 2))
        p.start()
        self.addCleanup(p.stop)

    def test_rows_match_the_xlsx_parsers_rules(self):
        # order 77 of 31 October was created at 00:50 on 1 November; the xlsx parser gives it business date 31 October
        rows, other = order_listing_ingest.rows_from_feed("2026-10-31", [row(), row(no="1", created="2026-10-31 13:10:33", amount="467.00")])
        self.assertEqual(other, 0)
        self.assertEqual([(r.order_no, r.order_type, r.amount, r.discount, r.status, r.business_date) for r in rows],
                         [("77", "Pick Up", Decimal("713.00"), Decimal("0.00"), "Printed", date(2026, 10, 31)),
                          ("1", "Pick Up", Decimal("467.00"), Decimal("0.00"), "Printed", date(2026, 10, 31))])

    def test_rows_on_another_business_date_are_counted_and_left_out(self):
        # 01:01 on 1 November is 19:01 on 31 October under the minus-6-hours rule, so for 1 November it is another day
        rows, other = order_listing_ingest.rows_from_feed("2026-11-01", [row(no="1", created="2026-11-01 01:01:13"), row(no="2", created="2026-11-01 13:00:00")])
        self.assertEqual((len(rows), other), (1, 1))

    def test_refuses_unfinished_days_and_bad_rows(self):
        with self.assertRaisesRegex(ValueError, "not finished"):
            order_listing_ingest.rows_from_feed("2026-11-02", [row()])
        for bad in (row(no="x"), row(created="soon"), row(amount="-5"), row(amount="abc")):
            with self.assertRaises(ValueError):
                order_listing_ingest.rows_from_feed("2026-10-31", [bad])
        with self.assertRaisesRegex(ValueError, "no rows"):
            order_listing_ingest.rows_from_feed("2026-10-31", [])


if __name__ == "__main__":
    unittest.main()
