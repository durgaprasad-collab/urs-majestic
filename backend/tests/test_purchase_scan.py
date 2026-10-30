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
                 "2.50", "11.71", "2.50", "11.71", "0.00", "0.00", "492.00", "-", "-", "Delivery and other charges", "-", "-", "-", "6.67", "2.50", "0.17", "2.50", "0.17", "0.00", "0.00", "7.00",
                 "Total", "3", "475.24"]
        result = parse_bill("\n".join(cells), [SimpleNamespace(name="Sunflower Oil"), SimpleNamespace(name="Vendor delivery charge")])
        self.assertEqual(result["lines"], [{"name": "Sunflower Oil", "qty": 3.0, "unit": "pcs", "amount": 492.0},
                                           {"name": "Vendor delivery charge", "qty": 1.0, "unit": "pcs", "amount": 7.0}])
        self.assertEqual(result["date"], "2026-10-30")
        self.assertEqual(result["bill_ref"], "C175222T27057314")
        self.assertNotIn("492.00", result["unparsed"])
        # a row whose cells do not add up is left for the buyer
        bad = parse_bill("\n".join(cells).replace("492.00", "500.00"), [])
        self.assertEqual([l["name"] for l in bad["lines"]], ["Delivery and other charges"])

    def test_bigbasket_invoice_bare_hsn_pack_sizes_and_doubled_headers(self):
        def row(*c):
            return list(c)
        cells = ["Invoice Number", "Invoice Number", "IBNTN26IODO01341", "Invoice Date", "Invoice Date", "2026-10-30",
                 "SI", "SI", "No.", "No.", "Item Description", "Item Description", "HSN", "HSN", "TOTAL", "TOTAL", "Value", "Value"]
        cells += row("1", "fresho! Lemon 250 g", "08051000", "1", "57.50", "46.44", "46.44", "0.00", "0.44", "46.44", "0.00%", "0.00", "0.00%", "0.00", "0.00", "46.44")
        cells += row("2", "fresho! Capsicum - Green", "250 g", "07099920", "4", "31.25", "18.68", "74.70", "0.00", "0.70", "74.70", "0.00%", "0.00", "0.00%", "0.00", "0.00", "74.70")
        cells += row("3", "Tata Salt Vacuum", "Evaporated Iodised Salt 1", "kg Pouch", "25010020", "2", "32.00", "31.30", "62.59", "0.00", "0.59", "62.59", "0.00%", "0.00", "0.00%", "0.00", "0.00", "62.59")
        cells += row("4", "Eastern Kashmiri Chilli", "Powder 250 g Pouch", "09042229", "2", "295.00", "155.99", "327.58", "0.00", "3.08", "311.98", "2.50%", "7.80", "2.50%", "7.80", "0.00", "327.58")
        cells += row("5", "Amul Curd - Creamy &", "Tasty 900 g Pouch", "04039090", "4", "58.00", "55.76", "234.20", "0.00", "2.20", "223.04", "2.50%", "5.58", "2.50%", "5.58", "0.00", "234.20")
        cells += ["7.01", "718.75", "745.51", "Handling Charge", "Handling Charge", "Rs.", "7.00"]
        items = [SimpleNamespace(name=n) for n in ("Lemon", "Capsicum", "Salt", "Iodised Salt", "Chilli Powder", "Curd")]
        result = parse_bill("\n".join(cells), items)
        self.assertEqual([(l["name"], l["qty"], l["unit"], l["amount"]) for l in result["lines"]], [
            ("Lemon", 250.0, "g", 46.44), ("Capsicum", 1.0, "kg", 74.7), ("Iodised Salt", 2.0, "kg", 62.59),
            ("Chilli Powder", 500.0, "g", 327.58), ("Curd", 3.6, "kg", 234.2)])
        self.assertEqual(result["date"], "2026-10-30")
        self.assertEqual(result["bill_ref"], "IBNTN26IODO01341")
        # The handling charge is already inside the item totals (their "other charges"
        # column sums to it), so it is not added as a second line.
        self.assertIn("Handling Charge", result["unparsed"])

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
