"""Read a photographed Petpooja slip into a draft; never writes orders.

Only exact, unambiguous menu matches are selected. Every parsed item remains
visible for review, including missing quantities and unmatched names.
"""
import base64
import io
import json
import logging
import re
import time
import unicodedata
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)


def read_photo(data: bytes, api_key: str) -> str:
    if not api_key:
        raise ValueError("Photo reading is not configured. Set OCR_SPACE_API_KEY on the server.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 25_000_000:
                raise ValueError("Photo is too large. Crop it to the slip and try again.")
            photo = ImageOps.exif_transpose(source).convert("RGB")
            photo.thumbnail((2000, 2000))
            out = io.BytesIO()
            photo.save(out, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Use a JPG or PNG photo of the slip.") from exc
    if out.tell() > 950_000:
        raise ValueError("Crop the photo to the slip and try again (image is too large).")
    body = urllib.parse.urlencode({
        "base64Image": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode(),
        "OCREngine": "2", "isTable": "true", "detectOrientation": "true",
        "scale": "true", "isOverlayRequired": "false",
    }).encode()
    req = urllib.request.Request("https://api.ocr.space/parse/image", data=body,
                                 headers={"apikey": api_key, "Content-Type": "application/x-www-form-urlencoded"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=18) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise ValueError("Invalid OCR response")
            break
        except urllib.error.HTTPError as exc:
            exc.close()
            logger.warning("Rush OCR HTTP status=%s attempt=%s", exc.code, attempt + 1)
            if exc.code == 429:
                raise ValueError("Photo reader has reached its usage limit. Try later or enter the order manually.") from exc
            if exc.code in (401, 403):
                raise ValueError("Photo reader access was refused. The server's OCR API key needs checking.") from exc
            if exc.code not in (408, 500, 502, 503, 504) or attempt == 1:
                raise ValueError("Photo-reading service could not accept the request. Retry the same photo or enter manually.") from exc
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            logger.warning("Rush OCR connection failure type=%s attempt=%s", type(exc).__name__, attempt + 1)
            if attempt == 1:
                raise ValueError("Could not connect to the photo reader after two attempts. Retry the same photo or enter manually.") from exc
        time.sleep(0.5)
    pages = result.get("ParsedResults") or []
    if result.get("IsErroredOnProcessing") or any(p.get("FileParseExitCode") != 1 for p in pages):
        errors = str(result.get("ErrorMessage", "")).lower()
        if any(word in errors for word in ("limit", "quota", "maximum number of requests")):
            raise ValueError("Photo reader has reached its usage limit. Try later or enter the order manually.")
        if any(word in errors for word in ("api key", "apikey")):
            raise ValueError("Photo reader setup needs attention. The server's OCR API key needs checking.")
        raise ValueError("Couldn't read the whole slip. Retake a clear, straight photo.")
    text = "\n".join(p.get("ParsedText", "") for p in pages).strip()
    if not text:
        raise ValueError("No printed text found. Move closer and retake the photo.")
    return text


def _key(name: str) -> str:
    # Regular is a POS serving label, but gravy/starter and piece counts matter.
    name = re.sub(r"\(\s*regular\s*\)", "", name, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def parse_slip(text: str, menu: list[dict]) -> dict:
    # OCR may return full-width brackets/digits in otherwise English receipts.
    text = unicodedata.normalize("NFKC", text)
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    menu_keys: dict[str, list[dict]] = {}
    for item in menu:
        menu_keys.setdefault(_key(item["name"]), []).append(item)
    # Reuse the restaurant's explicit POS names, including combo descriptions.
    # Never split these names at commas or guess a dish from its price.
    seed_path = Path(__file__).resolve().parents[2] / "data/menu_seed.json"
    if seed_path.is_file():
        aliases = json.loads(seed_path.read_text(encoding="utf-8"))["pos_name_map"]
        for printed, canonical in aliases.items():
            targets = [m for m in menu if m["name"] == canonical]
            if len(targets) == 1:
                key = _key(printed)
                matches = menu_keys.setdefault(key, [])
                if all(m["id"] != targets[0]["id"] for m in matches):
                    matches.append(targets[0])
    warnings, items, unassigned = [], [], []
    # Limit number lookup to its label's line (or the immediately following
    # line), so a missing reference never picks up a date, price or quantity.
    number = r"[ \t]*(?:Number|N[o0]\.?|#)?[ \t]*[:.#-]*[ \t]*(?:\r?\n[ \t]*)?(\d+)\b"
    kot = re.search(r"^[ \t]*K[ \t]*O[ \t]*T" + number, text, re.I | re.M)
    bill = re.search(r"^[ \t]*B[ \t]*[i1l|!][ \t]*[i1l|!][ \t]*[i1l|!]" + number, text, re.I | re.M)
    date = re.search(r"\bDate\s*:\s*([^\r\n\t]+)", text, re.I)
    # Invoice: item block follows Cashier / separator and ends before totals.
    # KOT: item block follows an Item/Qty heading. Unknown layouts stay visible
    # as raw text and require manual entry rather than inventing an order.
    start = next((i + 1 for i, s in enumerate(lines) if re.match(r"Cashier\s*:", s, re.I)), None)
    if start is None:
        start = next((i + 1 for i, s in enumerate(lines) if re.search(r"\b(?:item|particulars|description)\b", s, re.I)
                      and re.search(r"\b(?:qty|quantity)\b", s, re.I)), None)
    end = next((i for i, s in enumerate(lines) if start is not None and i >= start
                and re.match(r"(?:Total\s*Qty|Sub\s*Total|Grand\s*Total|Thank\s*you)\b", s, re.I)), len(lines))
    if start is None:
        warnings.append("Item table not recognised. Use the photo/text to enter the dishes manually.")
        block = []
    else:
        block = lines[start:end]
    pending = []
    parcel = False
    parcel_qty = 0

    def append_item(name, qty, quantity_seen=True):
        nonlocal parcel, parcel_qty
        name = name.strip()
        if _key(name) == "parcel":
            parcel = True
            parcel_qty += qty or 0
            return
        matches = menu_keys.get(_key(name), [])
        if not quantity_seen and not matches:
            # A loose header/separator fragment is not evidence of an item.
            # Keep it visible separately rather than inventing a cooking row.
            unassigned.append(name)
            return
        items.append({"name": name, "qty": qty, "menu_item_id": matches[0]["id"] if len(matches) == 1 else None})

    for line in block:
        if re.fullmatch(r"[\s=_.\-]+", line):
            continue
        # Retail invoice example: (Q) 1.0 x 149.0 = 149.0.
        q = re.search(r"(?:\(\s*[QO0]\s*\)\s*)?(\d+(?:\.\d+)?)\s*[x×]\s*\d", line, re.I)
        if q:
            prefix = line[:q.start()].strip()
            if pending and prefix and (_key(prefix) in menu_keys or _key(prefix) == "parcel"):
                append_item(" ".join(pending), None, quantity_seen=False)
                pending = []
            name = " ".join(pending + ([prefix] if prefix else []))
            n = float(q.group(1))
            if name:
                append_item(name, int(n) if n.is_integer() and 0 < n <= 999 else None)
            else:
                unassigned.append(line)
            pending = []
            continue
        # KOT table with item name followed by quantity (no currency guessing).
        q = re.match(r"^(.+?[A-Za-z].*?)\s{2,}(\d+(?:\.\d+)?)$", line)
        if q:
            if pending:
                append_item(" ".join(pending), None, quantity_seen=False)
                pending = []
            n = float(q.group(2))
            append_item(q.group(1), int(n) if n.is_integer() and 0 < n <= 999 else None)
            continue
        if re.search(r"[A-Za-z]", line):
            # Exact known name on its own begins a new row, not a continuation.
            if pending and (_key(line) in menu_keys or _key(line) == "parcel"):
                append_item(" ".join(pending), None, quantity_seen=False)
                pending = []
            pending.append(line)
        elif re.fullmatch(r"\d+(?:\.\d+)?", line) and pending:
            n = float(line)
            append_item(" ".join(pending), int(n) if n.is_integer() and 0 < n <= 999 else None)
            pending = []
    if pending:
        append_item(" ".join(pending), None, quantity_seen=False)
    if unassigned:
        warnings.append("Some OCR text could not be assigned to an item. Check the unassigned text and photo for any missing dish.")
    if not kot and bill:
        warnings.append("This is a bill number, not a KOT number. It is labelled Bill in the order reference.")
    if any(i["menu_item_id"] is None or i["qty"] is None for i in items):
        warnings.append("Check the highlighted dishes and quantities before using this draft.")
    total = re.search(r"Total\s*Qty\s*:\s*(\d+(?:\.\d+)?)", text, re.I)
    if total:
        warnings.append("Printed total quantity: " + total.group(1) + ". Check all lines, including packaging, against the photo.")
        counted = sum(i["qty"] or 0 for i in items) + parcel_qty
        if counted != float(total.group(1)):
            warnings.append(f"Quantity mismatch: captured {counted:g}, printed {total.group(1)}. Correct missing or extra items before sending.")
    reference = kot.group(1) if kot else "Bill " + bill.group(1) if bill else ""
    if not reference:
        warnings.append("Order number not read. Enter the KOT number or labelled bill reference above.")
    return {"kot_no": kot.group(1) if kot else "", "bill_no": bill.group(1) if bill else "", "order_reference": reference,
            "printed_date": date.group(1).strip() if date else "", "order_type": "Parcel" if parcel else "Dine-in",
            "items": items, "unassigned_text": unassigned, "warnings": warnings, "raw_text": text[:20000]}
