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


def _night(day, tandoor, kitchen):
    """Both cylinders read at 1 AM the morning after `day`'s night."""
    def row(gross):
        at = datetime.datetime(2026, 10, day + 1, 1, 0, tzinfo=IST)
        return {"recorded_at": at, "net": Decimal(str(gross)) - gas.TARE_KG, "night": datetime.date(2026, 10, day)}
    return row(tandoor), row(kitchen)


def _consolidate(*nights):
    readings = {"tandoor": [], "kitchen": []}
    for day, t, k in nights:
        rt, rk = _night(day, t, k)
        readings["tandoor"].append(rt)
        readings["kitchen"].append(rk)
    return gas.consolidated(readings)


class ConsolidatedTests(unittest.TestCase):
    def test_no_swap_is_sum_of_drops(self):
        n = _consolidate((10, 38.74, 29.13), (11, 29.57, 20.76))[-1]
        self.assertEqual(n["used"], Decimal("17.54"))
        self.assertEqual(n["fitted"], 0)

    def test_rotation_counts_one_new_cylinder(self):
        # Kitchen ran out, took the Tandoor's cylinder; Tandoor got a new one.
        n = _consolidate((11, 29.57, 20.76), (12, 35.86, 23.125))[-1]
        self.assertEqual(n["fitted"], 1)
        self.assertEqual(n["check"], "rotation")
        self.assertEqual(n["used"], Decimal("10.545"))
        self.assertEqual(n["split"]["tandoor"], Decimal("3.34"))
        self.assertEqual(n["split"]["kitchen"], Decimal("7.205"))
        self.assertEqual(n["returned"], Decimal("0.76"))

    def test_rotation_when_tandoor_reading_barely_moves(self):
        n = _consolidate((9, 38.8, 20.7), (10, 38.74, 29.13))[-1]
        self.assertEqual(n["fitted"], 1)
        self.assertEqual(n["used"], Decimal("10.83"))

    def test_kitchen_heavier_than_tandoor_was_breaks_rotation(self):
        n = _consolidate((9, 30.0, 20.5), (10, 28.0, 38.0))[-1]
        self.assertEqual(n["check"], "broken")
        self.assertEqual(n["fitted"], 1)

    def test_night_far_above_the_others_is_flagged(self):
        nights = _consolidate((9, 38.8, 20.7), (10, 38.74, 29.13), (11, 29.57, 20.76), (12, 35.86, 23.125))
        self.assertEqual([n["high"] for n in nights], [False, False, True, False])


if __name__ == "__main__":
    unittest.main()
