import datetime
import unittest
from zoneinfo import ZoneInfo

from app.services import stock_count as sc

IST = ZoneInfo("Asia/Kolkata")


def _item(name, cat, low=False, recount=False, stockable=True):
    return {"name": name, "category": cat, "f_low": low, "f_recount": recount, "stockable": stockable}


class SourceTests(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(sc.source_of("petpooja_usage:2026-10-08:0.6:kg"), "sales")
        self.assertEqual(sc.source_of("purchase_auto:123"), "purchase")
        self.assertEqual(sc.source_of("SYSTEM-COMPUTED repair"), "system")
        self.assertEqual(sc.source_of(None), "counted")
        self.assertEqual(sc.source_of("admin_edit"), "counted")
        self.assertEqual(sc.source_of("app_count"), "counted")


class CountDayTests(unittest.TestCase):
    def test_after_midnight_belongs_to_previous_evening(self):
        late = datetime.datetime(2026, 10, 10, 0, 40, tzinfo=IST)
        self.assertEqual(sc.count_day(late), datetime.date(2026, 10, 9))

    def test_evening_is_same_day(self):
        evening = datetime.datetime(2026, 10, 9, 22, 5, tzinfo=IST)
        self.assertEqual(sc.count_day(evening), datetime.date(2026, 10, 9))

    def test_utc_timestamp_is_converted(self):
        # 17:00 UTC = 22:30 IST on the same day
        utc = datetime.datetime(2026, 10, 9, 17, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(sc.count_day(utc), datetime.date(2026, 10, 9))


class TonightsListTests(unittest.TestCase):
    items = [
        _item("Onion", "Vegetables"), _item("Milk", "Dairy"), _item("Rice", "Dry Goods"),
        _item("Chilli powder", "Spices", low=True), _item("Oil", "Dry Goods", recount=True),
        _item("Apron", "Apparel", stockable=False),
    ]

    def test_daily_is_perishables_plus_flagged(self):
        kind, todo = sc.tonights_list(self.items, datetime.date(2026, 10, 9))  # Friday
        self.assertEqual(kind, "daily")
        self.assertEqual({i["name"] for i in todo}, {"Onion", "Milk", "Chilli powder", "Oil"})

    def test_monday_is_full_count_of_stockable_items(self):
        kind, todo = sc.tonights_list(self.items, datetime.date(2026, 10, 12))  # Monday
        self.assertEqual(kind, "full")
        self.assertEqual(len(todo), 5)
        self.assertNotIn("Apron", {i["name"] for i in todo})

    def test_sorted_by_category_order(self):
        _, todo = sc.tonights_list(self.items, datetime.date(2026, 10, 12))
        self.assertEqual([i["category"] for i in todo][:2], ["Vegetables", "Dairy"])


if __name__ == "__main__":
    unittest.main()
