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

    def test_new_cylinder_counts_old_remainder_plus_new_use(self):
        # Old had 1.9 kg left; new one weighed 38.6 -> 18.6 kg, so 0.6 already used.
        rows = gas.chain_usage([_r(8, 0, 30, 21.9), _r(9, 0, 30, 38.6, new=True)])
        self.assertEqual(rows[1]["used"], Decimal("1.9") + Decimal("0.6"))

    def test_heavier_without_new_flag_never_counts_negative(self):
        rows = gas.chain_usage([_r(8, 0, 30, 25.0), _r(9, 0, 30, 26.0)])
        self.assertEqual(rows[1]["used"], Decimal("0"))

    def test_gap_in_the_log_is_not_a_nights_use(self):
        rows = gas.chain_usage([_r(1, 0, 30, 35.0), _r(9, 0, 30, 25.0)])
        self.assertIsNone(rows[1]["used"])


if __name__ == "__main__":
    unittest.main()
