import io
import unittest
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


if __name__ == "__main__":
    unittest.main()
