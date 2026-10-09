import unittest

from app.services.today_page import _pick_tasks, TASKS_SHOWN, TASKS_PER_ROLE


def _task(role, n):
    return {"role": role, "title": f"{role}-{n}"}


class PickTasksTests(unittest.TestCase):
    def test_caps_tasks_per_role_and_total(self):
        # Already sorted by score: one role dominates the top of the list.
        tasks = [_task("bi", i) for i in range(6)] + [_task(r, 0) for r in ("gm", "coo", "cro", "creative")] + \
                [_task("gm", i) for i in range(1, 5)]
        shown = _pick_tasks(tasks)
        self.assertEqual(len(shown), TASKS_SHOWN)
        self.assertEqual(sum(t["role"] == "bi" for t in shown), TASKS_PER_ROLE)
        self.assertEqual({t["role"] for t in shown}, {"bi", "gm", "coo", "cro", "creative"})

    def test_keeps_score_order(self):
        tasks = [_task("gm", 0), _task("coo", 0), _task("gm", 1)]
        self.assertEqual([t["title"] for t in _pick_tasks(tasks)], ["gm-0", "coo-0", "gm-1"])

    def test_empty(self):
        self.assertEqual(_pick_tasks([]), [])


if __name__ == "__main__":
    unittest.main()
