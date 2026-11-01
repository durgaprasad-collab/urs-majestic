import unittest
from datetime import date
from unittest.mock import patch

from app.services import pos_ingest


class RowsFromFeedTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(pos_ingest, "business_today", return_value=date(2026, 11, 2))
        p.start()
        self.addCleanup(p.stop)

    def test_finished_day_rows_become_import_rows(self):
        rows = pos_ingest.rows_from_feed("2026-11-01", [["Gobi Manchurian ", "2026-11-01", "3", "387.00"], ["Parcel", "2026-11-01", "18", "1,080"]])
        self.assertEqual([(r["raw_name"], str(r["qty"]), str(r["revenue"])) for r in rows],
                         [("Gobi Manchurian", "3", "387.00"), ("Parcel", "18", "1080")])
        self.assertEqual({r["sale_date"] for r in rows}, {date(2026, 11, 1)})

    def test_refuses_a_day_that_has_not_finished(self):
        with self.assertRaisesRegex(ValueError, "not finished"):
            pos_ingest.rows_from_feed("2026-11-02", [["Parcel", "2026-11-02", "1", "10"]])

    def test_refuses_rows_from_another_day_or_bad_numbers(self):
        with self.assertRaisesRegex(ValueError, "dated"):
            pos_ingest.rows_from_feed("2026-11-01", [["Parcel", "2026-10-31", "1", "10"]])
        for bad in ([["Parcel", "2026-11-01", "x", "10"]], [["Parcel", "2026-11-01", "-1", "10"]], [["Parcel", "2026-11-01", "1"]], []):
            with self.assertRaises(ValueError):
                pos_ingest.rows_from_feed("2026-11-01", bad)


if __name__ == "__main__":
    unittest.main()
