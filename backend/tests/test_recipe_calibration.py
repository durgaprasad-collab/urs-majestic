import unittest
from datetime import datetime, timedelta, timezone

from app.services import recipe_calibration as rc

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def ledger(*stretch_specs, system_in=None):
    """Build a ledger: each spec is (opening, recipe_use, bought, closing). Counts bracket the stretches."""
    seq, t, bal = [], T0, None
    for i, (opening, used, bought, closing) in enumerate(stretch_specs):
        if i == 0:
            seq.append((opening, t, "count"))
        bal = opening
        t += timedelta(days=1)
        if used:
            bal -= used
            seq.append((bal, t, "use"))
        if bought:
            bal += bought
            seq.append((bal, t, "in"))
        if system_in == i:
            seq.append((bal, t, "sys"))
        t += timedelta(days=1)
        seq.append((closing, t, "count"))
    return seq


class StretchTests(unittest.TestCase):
    def test_really_used_is_opening_plus_in_minus_closing(self):
        w = rc.stretches(ledger((10, 3, 5, 9)))
        self.assertEqual(len(w), 1)
        self.assertAlmostEqual(w[0]["recipe"], 3)
        self.assertAlmostEqual(w[0]["real"], 10 + 5 - 9)

    def test_system_row_drops_the_stretch(self):
        self.assertEqual(rc.stretches(ledger((10, 3, 0, 6), system_in=0)), [])

    def test_stretches_opened_before_a_calibration_are_ignored(self):
        seq = ledger((10, 2, 0, 6), (6, 2, 0, 2))
        self.assertEqual(len(rc.stretches(seq)), 2)
        self.assertEqual(len(rc.stretches(seq, since=T0 + timedelta(days=2))), 1)

    def test_kinds(self):
        self.assertEqual([rc.kind_of(n) for n in ("zomato_usage:x", "purchase_auto 5", "transfer_out", "SYSTEM-REBUILD", "app_count", None)],
                         ["use", "in", "out", "sys", "count", "count"])


class DecideTests(unittest.TestCase):
    def wins(self, n, recipe, real):
        return [{"recipe": recipe, "real": real} for _ in range(n)]

    def test_consistent_overuse_is_a_scale_suggestion(self):
        v = rc.decide(self.wins(6, 2.0, 3.4))
        self.assertEqual(v["verdict"], "scale")
        self.assertAlmostEqual(v["ratio"], 1.7)

    def test_underuse_scales_down(self):
        self.assertLess(rc.decide(self.wins(5, 2.0, 0.8))["ratio"], 1)

    def test_too_few_stretches_or_close_enough_gives_nothing(self):
        self.assertIsNone(rc.decide(self.wins(4, 2.0, 4.0)))
        self.assertIsNone(rc.decide(self.wins(8, 2.0, 2.2)))

    def test_huge_gap_is_a_check_not_a_scale(self):
        v = rc.decide(self.wins(6, 0.05, 3.0))
        self.assertEqual(v["verdict"], "check")

    def test_mixed_direction_gives_nothing(self):
        w = self.wins(3, 2.0, 5.0) + self.wins(3, 2.0, 0.2)
        self.assertIsNone(rc.decide(w))

    def test_no_recipe_use_gives_nothing(self):
        self.assertIsNone(rc.decide(self.wins(6, 0.0, 3.0)))


class ChosenLinesTests(unittest.TestCase):
    dishes = [{"id": 1, "name": "A", "grams": 10.0, "new": 17.0}, {"id": 2, "name": "B", "grams": 20.0, "new": 34.0}]

    def test_no_pick_means_every_dish_at_the_suggested_grams(self):
        self.assertEqual([d["new"] for d in rc.chosen_lines(self.dishes, None)], [17.0, 34.0])

    def test_picked_lines_use_the_typed_grams_and_leave_the_rest(self):
        got = rc.chosen_lines(self.dishes, [{"id": 2, "grams": "25.5"}])
        self.assertEqual([(d["id"], d["new"]) for d in got], [(2, 25.5)])

    def test_refuses_other_dishes_bad_grams_and_empty_picks(self):
        for bad in ([{"id": 99, "grams": "5"}], [{"id": 1, "grams": "0"}], [{"id": 1, "grams": "6000"}],
                    [{"id": 1, "grams": "abc"}], [{"id": 1}], []):
            with self.assertRaises(ValueError):
                rc.chosen_lines(self.dishes, bad)


if __name__ == "__main__":
    unittest.main()
