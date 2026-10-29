"""Read a photographed Petpooja slip into a draft; never writes orders.

Only exact, unambiguous menu matches are selected. Every parsed item remains
visible for review, including missing quantities and unmatched names.
"""
import base64
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageOps, UnidentifiedImageError


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
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ValueError("Photo reader is unavailable. Try again or enter the order manually.") from exc
    pages = result.get("ParsedResults") or []
    if result.get("IsErroredOnProcessing") or any(p.get("FileParseExitCode") != 1 for p in pages):
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
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    menu_keys: dict[str, list[dict]] = {}
    for item in menu:
        menu_keys.setdefault(_key(item["name"]), []).append(item)
    warnings, items = [], []
    kot = re.search(r"\bK\s*O\s*T\s*(?:No\.?|Number|#)?\s*[:.#-]?\s*(\d+)\b", text, re.I)
    bill = re.search(r"\bBill\s*(?:No\.?|Number|#)\s*[:.#-]?\s*(\d+)\b", text, re.I)
    date = re.search(r"\bDate\s*:\s*([^\r\n]+)", text, re.I)
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

    def append_item(name, qty):
        nonlocal parcel
        name = name.strip()
        if _key(name) == "parcel":
            parcel = True
            return
        matches = menu_keys.get(_key(name), [])
        items.append({"name": name, "qty": qty, "menu_item_id": matches[0]["id"] if len(matches) == 1 else None})

    for line in block:
        if re.fullmatch(r"[\s=_.\-]+", line):
            continue
        # Retail invoice example: (Q) 1.0 x 149.0 = 149.0.
        q = re.match(r"(?:\(\s*Q\s*\)\s*)?(\d+(?:\.\d+)?)\s*[x×]\s*\d", line, re.I)
        if q and pending:
            n = float(q.group(1))
            append_item(" ".join(pending), int(n) if n.is_integer() and 0 < n <= 999 else None)
            pending = []
            continue
        # KOT table with item name followed by quantity (no currency guessing).
        q = re.match(r"^(.+?[A-Za-z].*?)\s{2,}(\d+(?:\.\d+)?)$", line)
        if q:
            if pending:
                append_item(" ".join(pending), None)
                pending = []
            n = float(q.group(2))
            append_item(q.group(1), int(n) if n.is_integer() and 0 < n <= 999 else None)
            continue
        if re.search(r"[A-Za-z]", line):
            # Exact known name on its own begins a new row, not a continuation.
            if pending and (_key(line) in menu_keys or _key(line) == "parcel"):
                append_item(" ".join(pending), None)
                pending = []
            pending.append(line)
        elif re.fullmatch(r"\d+(?:\.\d+)?", line) and pending:
            n = float(line)
            append_item(" ".join(pending), int(n) if n.is_integer() and 0 < n <= 999 else None)
            pending = []
    if pending:
        append_item(" ".join(pending), None)
    if not kot and bill:
        warnings.append("This is a bill number, not a KOT number. It is labelled Bill in the order reference.")
    if any(i["menu_item_id"] is None or i["qty"] is None for i in items):
        warnings.append("Check the highlighted dishes and quantities before using this draft.")
    total = re.search(r"Total\s*Qty\s*:\s*(\d+(?:\.\d+)?)", text, re.I)
    if total:
        warnings.append("Printed total quantity: " + total.group(1) + ". Check all lines, including packaging, against the photo.")
    return {"kot_no": kot.group(1) if kot else "", "bill_no": bill.group(1) if bill else "",
            "printed_date": date.group(1).strip() if date else "", "order_type": "Parcel" if parcel else "Dine-in",
            "items": items, "warnings": warnings, "raw_text": text[:20000]}
