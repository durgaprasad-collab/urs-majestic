import datetime
import unittest

from app.services import live_stock


class CleanRowsTests(unittest.TestCase):
    def test_report_table_rows(self):
        rows = live_stock.clean_rows([["Mushroom 65", "2026-10-27", "1.00", "119.00"], ["Parcel", "2026-10-27", "11.00", "110.00"]])
        self.assertEqual(rows[0], {"name": "Mushroom 65", "qty": 1.0, "amount": 119.0, "date": "2026-10-27"})
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


class ZomatoTests(unittest.TestCase):
    DAY = datetime.date(2026, 10, 27)

    def orders(self, *rows):
        return live_stock.clean_orders([{"id": i, "status": s, "placed": p, "items": it, "total": t} for i, s, p, it, t in rows])

    def test_counts_delivered_and_in_progress_not_rejected(self):
        rows, n, amount = live_stock.zomato_rows(self.orders(
            ("1", "DELIVERED", "2026-10-27T13:00", "2 x Butter Naan (1 No), 1 x Chilli Parotta", 300),
            ("2", "READY", "2026-10-27T21:36", "1 x Chilli Parotta", 171),
            ("3", "REJECTED", "2026-10-27T14:41", "1 x Chilli Parotta, 1 x Veg Noodles", 307),
            ("4", "CANCELLED", "2026-10-27T15:00", "1 x Veg Noodles", 100)), self.DAY)
        self.assertEqual(n, 2)
        self.assertEqual(amount, 471)
        self.assertEqual({r["name"]: r["qty"] for r in rows}, {"Butter Naan (1 No)": 2.0, "Chilli Parotta": 2.0})

    def test_business_day_is_1am_to_1am(self):
        _, n, _ = live_stock.zomato_rows(self.orders(
            ("1", "DELIVERED", "2026-10-27T00:49", "1 x A", 10),    # still the 26th's business day
            ("2", "DELIVERED", "2026-10-27T01:00", "1 x A", 10),    # first minute of the 27th
            ("3", "DELIVERED", "2026-10-28T00:30", "1 x A", 10),    # after midnight: still the 27th
            ("4", "DELIVERED", "2026-10-28T01:00", "1 x A", 10)), self.DAY)  # the 28th
        self.assertEqual(n, 2)

    def test_empty_list_is_fine(self):
        self.assertEqual(live_stock.zomato_rows([], self.DAY), ([], 0, 0.0))

    def test_rejects_junk(self):
        for bad in ([{"id": "x", "placed": "2026-10-27T12:00", "items": "1 x A", "total": 1}],
                    [{"id": "1", "placed": "nope", "items": "1 x A", "total": 1}], "x"):
            with self.assertRaises((ValueError, TypeError)):
                live_stock.clean_orders(bad)

    def test_unknown_channel(self):
        with self.assertRaises(ValueError):
            live_stock.save_read(None, None, [], channel="swiggy")


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
