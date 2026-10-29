import io
import unittest
from pathlib import Path
from html.parser import HTMLParser
from unittest.mock import patch

from PIL import Image
from app.services.rush_scan import parse_slip, read_photo


MENU = [{"id": 1, "name": "Gobi Fried Rice"}, {"id": 2, "name": "Paneer Butter Masala"}]
SAMPLE = """SHIVA SIR PENDING
Retail Invoice
URS MAJESTIC
Customer: shiva sir, french village owner
Date: 09-10-2026 15:15
Pick Up
Bill No.: 7
Cashier: biller
====================
Gobi Fried Rice (Regular)
(Q) 1.0 x 149.0 = 149.0
====================
Total Qty : 1.0
Sub Total : 149.00
149.00@ CGST 2.50% = 3.73
Grand Total : 156.00
"""


class ParseTests(unittest.TestCase):
    def test_order_reference_handles_ocr_label_variations(self):
        for label in ("Bill No.: 015", "B1ll N0.: 015", "Bi11 No : 015", "B i l l No. : 015", "B|ll No.\n015"):
            with self.subTest(label=label):
                r = parse_slip(label + "\nCashier: biller\n", MENU)
                self.assertEqual(r["order_reference"], "Bill 015")
        self.assertEqual(parse_slip("KOT No.: 42\nBill No.: 15", MENU)["order_reference"], "42")

    def test_missing_reference_never_uses_other_numbers(self):
        r = parse_slip("Bill No.:\nDate: 29-10-2026\nCashier: biller\nGobi Fried Rice\n(Q) 1.0 x 149.0 = 149.0", MENU)
        self.assertEqual(r["order_reference"], "")
        self.assertTrue(any("Order number not read" in w for w in r["warnings"]))

    def test_all_established_combo_descriptions_stay_one_combo(self):
        import json
        seed = Path(__file__).parents[1] / "data/menu_seed.json"
        aliases = json.loads(seed.read_text(encoding="utf-8"))["pos_name_map"]
        for printed, canonical in aliases.items():
            if not canonical.startswith("Combo "):
                continue
            with self.subTest(combo=canonical):
                raw = f"Bill No.: 20\nCashier: biller\n{printed}\n(Q) 2.0 x 149.0 = 298.0\nTotal Qty : 2.0"
                r = parse_slip(raw, [{"id": 50, "name": canonical}])
                self.assertEqual(len(r["items"]), 1)
                self.assertEqual(r["items"][0]["menu_item_id"], 50)
                self.assertEqual(r["items"][0]["qty"], 2)
                self.assertEqual(r["order_type"], "Dine-in")

    def test_combo_bill_maps_pos_description_to_one_combo(self):
        raw = "Retail Invoice\nDate: 29-10-2026 20:51\nPick Up\nBill No.: 14\nCashier: biller\nRoti, Dal Tadka Jeera Rice, Paneer Subzi S Al Ad\n(Q) 1.0 x 149.0 = 149.0\nTotal Qty : 1.0\nGrand Total : 156.00"
        r = parse_slip(raw, [{"id": 10, "name": "Combo 01 - Mini North Indian Meal"}])
        self.assertEqual(r["bill_no"], "14")
        self.assertEqual(r["order_type"], "Dine-in")
        self.assertEqual(len(r["items"]), 1)
        self.assertEqual(r["items"][0]["menu_item_id"], 10)
        self.assertEqual(r["items"][0]["qty"], 1)

    def test_oct29_photo_ocr_bill_typo_and_full_width_quantity(self):
        raw = "Retail Invoice\nDate: 29-10-2026 20:24\nPick Up\nB1ll No.: 13\nCashier: biller\nChappati (2) With Channa\n\uff08Q\uff09 1.0 x 89.0 =89.0\nTotal Qty : 1.0\nSub Total : 89.00"
        r = parse_slip(raw, [{"id": 4, "name": "Chappati (2) With Channa"}])
        self.assertEqual(r["bill_no"], "13")
        self.assertEqual(r["kot_no"], "")
        self.assertEqual(r["order_type"], "Dine-in")
        self.assertEqual(r["items"], [{"name": "Chappati (2) With Channa", "qty": 1, "menu_item_id": 4}])

    def test_sample_bill_is_dine_in_and_bill_is_not_kot(self):
        r = parse_slip(SAMPLE, MENU)
        self.assertEqual(r["kot_no"], "")
        self.assertEqual(r["bill_no"], "7")
        self.assertEqual(r["order_type"], "Dine-in")
        self.assertEqual(r["items"], [{"name": "Gobi Fried Rice (Regular)", "qty": 1, "menu_item_id": 1}])

    def test_parcel_item_is_takeaway_and_not_food(self):
        r = parse_slip(SAMPLE.replace("Total Qty", "Parcel\n(Q) 1.0 x 10.0 = 10.0\nTotal Qty"), MENU)
        self.assertEqual(r["order_type"], "Parcel")
        self.assertEqual(len(r["items"]), 1)

    def test_parcel_outside_item_table_does_not_classify(self):
        self.assertEqual(parse_slip("Parcel\n" + SAMPLE, MENU)["order_type"], "Dine-in")

    def test_unknown_dish_kept_for_review(self):
        r = parse_slip(SAMPLE.replace("Gobi Fried Rice (Regular)", "G0bi Frled Rice"), MENU)
        self.assertIsNone(r["items"][0]["menu_item_id"])

    def test_ambiguous_menu_not_guessed(self):
        r = parse_slip(SAMPLE, MENU + [{"id": 3, "name": "Gobi Fried Rice (Regular)"}])
        self.assertIsNone(r["items"][0]["menu_item_id"])

    def test_missing_or_fractional_qty_not_guessed(self):
        for line in ("", "(Q) 0.5 x 149 = 74.5"):
            r = parse_slip(SAMPLE.replace("(Q) 1.0 x 149.0 = 149.0", line), MENU)
            self.assertIsNone(r["items"][0]["qty"])

    def test_kot_table_and_packaging(self):
        r = parse_slip("KOT No: 42\nPick Up\nItem Name    Qty\nGobi Fried Rice    2\nParcel    1\n", MENU)
        self.assertEqual(r["kot_no"], "42")
        self.assertEqual(r["order_type"], "Parcel")
        self.assertEqual(r["items"][0]["qty"], 2)

    def test_unknown_layout_does_not_invent_items(self):
        r = parse_slip("Gobi Fried Rice\n149\n", MENU)
        self.assertEqual(r["items"], [])
        self.assertTrue(r["warnings"])

    def test_no_upload_for_invalid_image(self):
        with patch("urllib.request.urlopen") as request:
            with self.assertRaises(ValueError):
                read_photo(b"not a photo", "test")
            request.assert_not_called()

    def test_ocr_failure_is_actionable_and_not_partial(self):
        data = io.BytesIO()
        Image.new("RGB", (20, 20)).save(data, format="PNG")
        response = io.BytesIO(b'{"IsErroredOnProcessing":true,"ParsedResults":[]}')
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "whole slip"):
                read_photo(data.getvalue(), "test")


class ScanRouteTests(unittest.TestCase):
    def test_signed_out_does_not_send_photo_to_ocr(self):
        from app.web import rush_routes
        from fastapi import UploadFile
        with patch.object(rush_routes, "_owner", return_value=None), patch.object(rush_routes.rush_scan, "read_photo") as read:
            response = rush_routes.scan(None, UploadFile(file=io.BytesIO(b"photo")), None)
            self.assertEqual(response.status_code, 401)
            read.assert_not_called()

    def test_scan_returns_draft_without_creating_order(self):
        import json
        from app.web import rush_routes
        from fastapi import UploadFile
        with patch.object(rush_routes, "_owner", return_value=object()), \
             patch.object(rush_routes.rush_scan, "read_photo", return_value=SAMPLE), \
             patch.object(rush_routes.rush, "menu", return_value=MENU), \
             patch.object(rush_routes.rush, "create_order") as create:
            response = rush_routes.scan(None, UploadFile(file=io.BytesIO(b"photo")), None)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.body)["order_type"], "Dine-in")
            create.assert_not_called()

    def test_photo_limit_checked_before_ocr(self):
        from app.web import rush_routes
        from fastapi import UploadFile
        with patch.object(rush_routes, "_owner", return_value=object()), \
             patch.object(rush_routes.settings, "UPLOAD_MAX_MB", 1), \
             patch.object(rush_routes.rush_scan, "read_photo") as read:
            response = rush_routes.scan(None, UploadFile(file=io.BytesIO(b"x" * (1024 * 1024 + 1))), None)
            self.assertEqual(response.status_code, 413)
            read.assert_not_called()


class CameraInputTests(unittest.TestCase):
    def test_native_camera_input_uses_capacitor_capture_type(self):
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.inputs = {}

            def handle_starttag(self, tag, attrs):
                if tag == "input":
                    attrs = dict(attrs)
                    self.inputs[attrs.get("id")] = attrs

        parser = Inputs()
        template = Path(__file__).parents[1] / "app/web/templates/rush.html"
        parser.feed(template.read_text(encoding="utf-8"))
        photo = parser.inputs["photo"]
        self.assertEqual(photo["accept"], "image/*")
        self.assertEqual(photo["capture"], "environment")
        self.assertNotIn("capture", parser.inputs["photofile"])

    def test_android_can_find_system_camera(self):
        import xml.etree.ElementTree as ET
        manifest = Path(__file__).parents[2] / "rush-app/android/app/src/main/AndroidManifest.xml"
        root = ET.parse(manifest).getroot()
        actions = root.findall("queries/intent/action")
        name = "{http://schemas.android.com/apk/res/android}name"
        self.assertIn("android.media.action.IMAGE_CAPTURE", [a.get(name) for a in actions])


if __name__ == "__main__":
    unittest.main()
