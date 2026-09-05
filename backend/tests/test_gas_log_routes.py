import unittest
from datetime import date

from app.web.gas_log_routes import _three_calendar_month_window_start


class GasLogWindowTests(unittest.TestCase):
    def test_uses_current_and_two_preceding_calendar_months(self):
        self.assertEqual(
            _three_calendar_month_window_start(date(2026, 9, 5)),
            date(2026, 7, 1),
        )

    def test_crosses_year_boundary(self):
        self.assertEqual(
            _three_calendar_month_window_start(date(2027, 1, 20)),
            date(2026, 11, 1),
        )


if __name__ == "__main__":
    unittest.main()
