import datetime
import unittest
from unittest.mock import MagicMock

from app.services import rush
from app.services.rush_scan import parse_slip


def result(rows):
    r = MagicMock()
    r.mappings.return_value.all.return_value = rows
    r.mappings.return_value.__iter__.return_value = iter(rows)
    return r


class PassChecklistTests(unittest.TestCase):
    def test_water_retained_at_pass_but_never_sent_to_cooking(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        db = MagicMock()
        order = {"id": 1, "order_type": "Dine-in", "ref": None, "kot_no": "Bill 15", "created_at": now, "by": "Owner"}
        def line(mid, name, food):
            return {"line_id": mid, "order_id": 1, "qty": 1, "ready_at": None, "id": mid,
                    "name": name, "is_food": food, "st": "W", "cook": 5}
        db.execute.side_effect = [result([order]), result([line(1, "Veg Fried Rice", True), line(2, "Water Bottle", False)]), result([])]
        b = rush.board(db)
        self.assertEqual([i["name"] for i in b["pack"][0]["items"]], ["Veg Fried Rice", "Water Bottle"])
        self.assertTrue(b["pack"][0]["items"][1]["pass_only"])
        self.assertEqual([x["dish"] for s in b["slips"] for x in s["batches"]], ["Veg Fried Rice"])
        self.assertEqual(b["orders"][0]["total"], 2)
        self.assertEqual(b["orders"][0]["ready"], 0)

    def test_menu_and_photo_recognise_water_as_pass_item(self):
        db = MagicMock()
        db.execute.side_effect = [result([{"id": 2, "name": "Water Bottle", "category": "Non-Food",
                                          "is_food": False, "st": None, "cook": None, "sold": 1}]), result([])]
        menu = rush.menu(db)
        self.assertTrue(menu[0]["pass_only"])
        sql = str(db.execute.call_args_list[0].args[0])
        self.assertIn("lower(trim(mi.name)) <> 'parcel'", sql)
        draft = parse_slip("Bill No.: 15\nCashier: biller\nWater Bottle\n(Q) 1.0 x 20.0 = 20.0\nTotal Qty : 1.0", menu)
        self.assertEqual(draft["items"][0]["menu_item_id"], 2)
        self.assertEqual(draft["items"][0]["qty"], 1)
        self.assertEqual(draft["order_type"], "Dine-in")


if __name__ == "__main__":
    unittest.main()
