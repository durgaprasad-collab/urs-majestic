import unittest

from app.services import live_stock


class CleanRowsTests(unittest.TestCase):
    def test_report_table_rows(self):
        rows = live_stock.clean_rows([["Mushroom 65", "2026-10-27", "1.00", "119.00"], ["Parcel", "2026-10-27", "11.00", "110.00"]])
        self.assertEqual(rows[0], {"name": "Mushroom 65", "qty": 1.0, "amount": 119.0})
        self.assertEqual(rows[1]["qty"], 11.0)

    def test_objects_and_thousands_separator(self):
        rows = live_stock.clean_rows([{"name": " Gobi 65 ", "qty": "2", "amount": "1,099.50"}])
        self.assertEqual(rows[0], {"name": "Gobi 65", "qty": 2.0, "amount": 1099.5})

    def test_rejects_junk(self):
        for bad in (None, [], "x", [["", "d", "1", "1"]], [["A", "d", "abc", "1"]], [["A", "d", "-1", "1"]], [["A", "d", "nan", "1"]]):
            with self.assertRaises((ValueError, TypeError)):
                live_stock.clean_rows(bad)

    def test_too_many_rows(self):
        with self.assertRaises(ValueError):
            live_stock.clean_rows([["A", "d", "1", "1"]] * (live_stock.MAX_ROWS + 1))


class ClassifyTests(unittest.TestCase):
    def test_out_now(self):
        self.assertEqual(live_stock.classify(0.0, 1.0, 2.0)[0], "out")
        self.assertEqual(live_stock.classify(-0.2, None, None)[0], "out")

    def test_out_before_close(self):
        self.assertEqual(live_stock.classify(1.0, -0.1, 2.0), ("out", "Out before close"))

    def test_under_a_day_at_close(self):
        self.assertEqual(live_stock.classify(2.0, 0.6, 2.4), ("tight", "Under a day at close"))

    def test_ok(self):
        self.assertEqual(live_stock.classify(5.0, 3.0, 2.0), ("ok", "OK"))
        self.assertEqual(live_stock.classify(1.0, None, None), ("ok", "OK"))  # early in the day: no projection


if __name__ == "__main__":
    unittest.main()
