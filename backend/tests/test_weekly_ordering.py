import unittest
from datetime import date, timedelta

from app.services.weekly_ordering import (
    forecast_series, round_to_increment, split_delivery_quantities,
)


class WeeklyOrderingTests(unittest.TestCase):
    def test_rounds_up_to_supplier_increment(self):
        self.assertEqual(round_to_increment(1.01, 0.5), 1.5)
        self.assertEqual(round_to_increment(0, 0.5), 0)

    def test_three_drops_reconcile_exactly(self):
        parts = split_delivery_quantities(7.5, [2, 2, 1, 1, 3, 2, 1], 0.5)
        self.assertEqual(len(parts), 3)
        self.assertAlmostEqual(sum(parts), 7.5)
        self.assertTrue(all(part >= 0 for part in parts))

    def test_forecast_is_nonnegative_and_seven_days(self):
        start = date(2026, 6, 1)
        dates = [start + timedelta(days=i) for i in range(63)]
        values = [2.0 if d.weekday() < 5 else 4.0 for d in dates]
        future = [dates[-1] + timedelta(days=i + 1) for i in range(7)]
        result = forecast_series(values, dates, future)
        self.assertEqual(len(result["daily"]), 7)
        self.assertTrue(all(value >= 0 for value in result["daily"]))
        self.assertAlmostEqual(result["total"], sum(result["daily"]))
        self.assertGreaterEqual(result["high"], result["total"])
        self.assertLessEqual(result["low"], result["total"])


if __name__ == "__main__":
    unittest.main()
