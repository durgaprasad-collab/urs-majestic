import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.web import rush_routes


def request_for(body):
    async def stream():
        yield body
    return SimpleNamespace(stream=stream)


class LocalOCRTests(unittest.TestCase):
    def test_text_only_draft_does_not_call_external_ocr_or_create_order(self):
        text = "Bill No.: 15\nCashier: biller\nWater Bottle\n(Q) 1 x 20 = 20\nTotal Qty : 1"
        request = request_for(json.dumps({"text": text}).encode())
        with patch.object(rush_routes, "_owner", return_value=object()), \
             patch.object(rush_routes.rush, "menu", return_value=[{"id": 3, "name": "Water Bottle"}]), \
             patch.object(rush_routes.rush_scan, "read_photo") as external, \
             patch.object(rush_routes.rush, "create_order") as create:
            response = asyncio.run(rush_routes.scan_text(request, None))
            self.assertEqual(response.status_code, 200)
            draft = json.loads(response.body)
            self.assertEqual(draft["order_reference"], "Bill 15")
            self.assertEqual(draft["items"][0]["menu_item_id"], 3)
            external.assert_not_called()
            create.assert_not_called()

    def test_text_scan_requires_owner(self):
        with patch.object(rush_routes, "_owner", return_value=None):
            self.assertEqual(asyncio.run(rush_routes.scan_text(None, None)).status_code, 401)

    def test_reject_bad_or_oversized_text_without_querying_menu(self):
        for body, status in ((b"not json", 400), (b"[]", 400), (b'{"text":null}', 400),
                             (b'{"text":""}', 400), (json.dumps({"text": "x" * 20001}).encode(), 400),
                             (b"x" * 65537, 413)):
            with self.subTest(status=status), patch.object(rush_routes, "_owner", return_value=object()), \
                 patch.object(rush_routes.rush, "menu") as menu:
                self.assertEqual(asyncio.run(rush_routes.scan_text(request_for(body), None)).status_code, status)
                menu.assert_not_called()


if __name__ == "__main__":
    unittest.main()
