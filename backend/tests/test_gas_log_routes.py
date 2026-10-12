import datetime
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services import gas

IST = ZoneInfo("Asia/Kolkata")


def _r(day, hour, minute, gross, new=False):
    at = datetime.datetime(2026, 10, day, hour, minute, tzinfo=IST)
    return {"recorded_at": at, "net": Decimal(str(gross)) - gas.TARE_KG, "is_new_cylinder": new, "used": None}


class NightTests(unittest.TestCase):
    def test_half_past_midnight_belongs_to_previous_night(self):
        self.assertEqual(gas.night_of(datetime.datetime(2026, 10, 10, 0, 30, tzinfo=IST)), datetime.date(2026, 10, 9))

    def test_evening_reading_is_same_night(self):
        self.assertEqual(gas.night_of(datetime.datetime(2026, 10, 9, 23, 50, tzinfo=IST)), datetime.date(2026, 10, 9))


class ChainTests(unittest.TestCase):
    def test_use_is_drop_in_weight(self):
        rows = gas.chain_usage([_r(8, 0, 30, 31.4), _r(9, 0, 32, 29.0)])
        self.assertIsNone(rows[0]["used"])
        self.assertEqual(rows[1]["used"], Decimal("2.4"))

    def test_new_tick_is_ignored(self):
        # Staff tick "new" every night; a drop is still just the drop.
        rows = gas.chain_usage([_r(8, 0, 30, 38.7), _r(9, 0, 30, 29.6, new=True)])
        self.assertEqual(rows[1]["used"], Decimal("9.1"))
        self.assertFalse(rows[1]["swap"])

    def test_heavier_by_more_than_2kg_is_a_swap_with_no_usage(self):
        rows = gas.chain_usage([_r(8, 0, 30, 21.9), _r(9, 0, 30, 38.6)])
        self.assertTrue(rows[1]["swap"])
        self.assertIsNone(rows[1]["used"])

    def test_slightly_heavier_never_counts_negative(self):
        rows = gas.chain_usage([_r(8, 0, 30, 25.0), _r(9, 0, 30, 26.0)])
        self.assertEqual(rows[1]["used"], Decimal("0"))
        self.assertFalse(rows[1]["swap"])

    def test_gap_in_the_log_is_not_a_nights_use(self):
        rows = gas.chain_usage([_r(1, 0, 30, 35.0), _r(9, 0, 30, 25.0)])
        self.assertIsNone(rows[1]["used"])


if __name__ == "__main__":
    unittest.main()
