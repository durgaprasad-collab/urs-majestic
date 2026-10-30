import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pypdf import PdfWriter
from app.services.purchase_scan import parse_bill, read_bill


class PurchaseScanTests(unittest.TestCase):
    def test_extracts_explicit_units_and_preserves_ambiguous_lines(self):
        result = parse_bill("Shop\nRice 2 kg 80.00\nMystery 3 pcs 10 30\nMilk 2 60\nTotal 170", [SimpleNamespace(name="Rice")])
        self.assertEqual(result["lines"], [
            {"name": "Rice", "qty": 2.0, "unit": "kg", "amount": 80.0},
            {"name": "Mystery", "qty": 3.0, "unit": "pcs", "amount": 30.0}])
        self.assertIn("Milk 2 60", result["unparsed"])
        self.assertIn("Total 170", result["unparsed"])

    def test_handwritten_shapes_glued_units_and_trailing_marks(self):
        items = [SimpleNamespace(name="Tomato"), SimpleNamespace(name="Pumpkin")]
        result = parse_bill("Tomato 3kg 72-\nTomatoe 2 kg 50/-\nPumpkin 2pes 100\n| Rice 1 kg 40 |", items)
        self.assertEqual(result["lines"], [
            {"name": "Tomato", "qty": 3.0, "unit": "kg", "amount": 72.0},
            {"name": "Tomato", "qty": 2.0, "unit": "kg", "amount": 50.0},
            {"name": "Pumpkin", "qty": 2.0, "unit": "pcs", "amount": 100.0},
            {"name": "Rice", "qty": 1.0, "unit": "kg", "amount": 40.0}])

    def test_blinkit_gst_invoice_cells_on_separate_lines(self):
        cells = ["Invoice Number:", "C175222T27057314", "Invoice Date", ": 2026-10-30", "Sr.", "no", "Item Description", "MRP", "Total",
                 "1", "8906035030", "352", "Be Rite Refined Sunflower", "Oil(Pouch) (HSN-15121910)", "215.00", "153.00", "3", "468.57",
                 "2.50", "11.71", "2.50", "11.71", "0.00", "0.00", "492.00", "-", "-", "Delivery and other charges", "Total", "3", "475.24"]
        result = parse_bill("\n".join(cells), [SimpleNamespace(name="Sunflower Oil")])
        self.assertEqual(result["lines"], [{"name": "Sunflower Oil", "qty": 3.0, "unit": "pcs", "amount": 492.0}])
        self.assertEqual(result["date"], "2026-10-30")
        self.assertEqual(result["bill_ref"], "C175222T27057314")
        self.assertNotIn("492.00", result["unparsed"])
        # a row whose cells do not add up is left for the buyer
        bad = parse_bill("\n".join(cells).replace("492.00", "500.00"), [])
        self.assertEqual(bad["lines"], [])

    def test_duplicate_names_are_not_silently_chosen(self):
        result = parse_bill("RICE 2 kg 80", [SimpleNamespace(name="Rice"), SimpleNamespace(name="rice")])
        self.assertEqual(result["lines"][0]["name"], "RICE")

    def test_images_use_existing_reader(self):
        with patch("app.services.purchase_scan.read_photo", return_value="Rice 2 kg 80") as read:
            self.assertEqual(read_bill(b"image", "key"), "Rice 2 kg 80")
            read.assert_called_once_with(b"image", "key")

    def test_bad_pdf_is_actionable(self):
        with self.assertRaisesRegex(ValueError, "PDF"):
            read_bill(b"%PDFbroken", "")

    def test_pdf_limit(self):
        pdf = PdfWriter()
        for _ in range(16):
            pdf.add_blank_page(width=100, height=100)
        out = io.BytesIO()
        pdf.write(out)
        with self.assertRaisesRegex(ValueError, "15 pages"):
            read_bill(out.getvalue(), "")


class PurchaseRouteTests(unittest.TestCase):
    def test_scan_requires_sign_in(self):
        import asyncio
        from app.web import purchase_routes as routes
        with patch.object(routes, "require_user", return_value=(None, object())):
            self.assertEqual(asyncio.run(routes.purchase_scan_text(None, None)).status_code, 401)

    def test_phone_text_is_draft_without_writes(self):
        import asyncio
        from app.web import purchase_routes as routes
        from unittest.mock import MagicMock, AsyncMock
        db = MagicMock()
        request = SimpleNamespace(json=AsyncMock(return_value={"text": "Rice 2 kg 80"}))
        with patch.object(routes, "require_user", return_value=(SimpleNamespace(id=1), None)), patch.object(routes, "_ingredient_options", return_value=[SimpleNamespace(name="Rice")]):
            result = asyncio.run(routes.purchase_scan_text(request, db))
        self.assertEqual(result["lines"][0]["amount"], 80)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_storage_and_review_required_before_writing(self):
        import asyncio
        from datetime import date
        from unittest.mock import AsyncMock, MagicMock
        from app.web import purchase_routes as routes
        for extra in ({"storage_location": "warehouse"}, {"storage_location": "home", "ocr_text": "Rice", "items_confirmed": False}):
            db = MagicMock()
            request = SimpleNamespace(json=AsyncMock(return_value={"date": "2026-10-30", **extra}))
            with patch.object(routes, "require_user", return_value=(SimpleNamespace(id=1), None)), patch.object(routes, "business_today", return_value=date(2026, 10, 30)):
                result = asyncio.run(routes.save_bill(request, db))
            self.assertEqual(result.status_code, 400)
            db.add.assert_not_called()
            db.commit.assert_not_called()
