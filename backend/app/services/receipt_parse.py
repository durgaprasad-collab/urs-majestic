"""Receipt OCR + heuristic line-item parsing.

Tesseract turns a receipt image into raw text; that text has no structure, so
the parsing here is deliberately best-effort -- regex for qty/unit/price and a
fuzzy name match to the ingredient list. It pre-fills a review table the owner
corrects; it NEVER creates purchases directly. `parse_receipt_text` is pure
(no tesseract), so it is unit-testable on sample strings.
"""
import difflib
import io
import re
from dataclasses import dataclass

# Free-text unit token -> canonical unit enum value.
_UNIT_MAP = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "l": "l", "ltr": "l", "ltrs": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "ml": "ml",
    "pc": "pcs", "pcs": "pcs", "pce": "pcs", "piece": "pcs", "pieces": "pcs",
    "no": "pcs", "nos": "pcs", "packet": "pcs", "packets": "pcs",
    "pkt": "pcs", "pkts": "pcs", "pack": "pcs", "packs": "pcs",
}
_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(_UNIT_MAP, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_HAS_DIGIT = re.compile(r"\d")

# Receipt boilerplate — a line whose name reduces to one of these is a total /
# tax / footer, not an item, so it's dropped before it reaches the review table.
_SKIP_WORDS = {
    "total", "sub total", "subtotal", "grand total", "net total", "net amount",
    "tax", "gst", "cgst", "sgst", "igst", "vat", "amount", "amount payable",
    "payable", "cash", "card", "upi", "change", "balance", "discount",
    "round off", "roundoff", "round", "net", "bill", "bill amount", "paid",
    "tender", "qty", "rate", "item", "items", "invoice", "receipt", "date",
    "thank you", "thanks",
}


@dataclass
class ReceiptLine:
    raw: str
    name: str = ""
    qty: float | None = None
    unit: str | None = None
    total_price: float | None = None
    ingredient_id: int | None = None
    ingredient_name: str | None = None


_OCR_ENDPOINT = "https://api.ocr.space/parse/image"


def _shrink(image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """Downscale + JPEG-compress so the upload stays under OCR.space's free-tier
    size cap and OCRs faster. Falls back to the original bytes if Pillow can't
    open it."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((2200, 2200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, content_type or "image/png"


def ocr_image(image_bytes: bytes, content_type: str = "image/png") -> str:
    """Raw OCR text via OCR.space (a free hosted OCR API — no system binary, so
    it runs on a plain Python host). Raises on an API/transport error; the caller
    treats any failure as 'OCR unavailable' and falls back to manual entry."""
    import base64
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    from app.core.config import settings

    data_bytes, ctype = _shrink(image_bytes, content_type)
    b64 = base64.b64encode(data_bytes).decode("ascii")
    body = urllib.parse.urlencode({
        "apikey": settings.OCR_SPACE_API_KEY or "helloworld",
        "base64Image": f"data:{ctype};base64,{b64}",
        "language": "eng",
        "isTable": "true",   # receipts are tabular -- keeps columns aligned
        "scale": "true",
        "OCREngine": "2",
    }).encode()
    req = urllib.request.Request(
        _OCR_ENDPOINT, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.load(resp)

    if payload.get("IsErroredOnProcessing"):
        err = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "OCR failed"
        raise RuntimeError(err if isinstance(err, str) else "; ".join(err))
    return "\n".join(r.get("ParsedText", "") for r in payload.get("ParsedResults") or [])


def parse_receipt_text(text: str, ingredient_names: dict[str, int]) -> list[ReceiptLine]:
    """Best-effort candidate line items from OCR text.

    `ingredient_names` maps lower-cased ingredient name -> id, for fuzzy matching.
    A line is kept only if it yields a price OR a qty+unit -- so headers, totals
    and noise are dropped. Everything here is a guess for the review screen.
    """
    lc_names = list(ingredient_names.keys())
    out: list[ReceiptLine] = []

    for raw in text.splitlines():
        s = raw.strip()
        if len(s) < 3 or not _HAS_DIGIT.search(s):
            continue

        rl = ReceiptLine(raw=s)
        name_cut = len(s)

        # qty + unit, e.g. "2 kg", "500g", "1 ltr"
        um = _UNIT_RE.search(s)
        if um:
            try:
                rl.qty = float(um.group(1))
            except ValueError:
                rl.qty = None
            rl.unit = _UNIT_MAP.get(um.group(2).lower())
            name_cut = min(name_cut, um.start())

        # price = the last number that comes AFTER the qty token (receipts put the
        # amount at the end); guarding against picking the qty itself as the price.
        qty_end = um.end() if um else 0
        price_m = None
        for m in _NUMBER_RE.finditer(s):
            if m.start() >= qty_end:
                price_m = m
        if price_m:
            try:
                rl.total_price = float(price_m.group(1))
                name_cut = min(name_cut, price_m.start())
            except (TypeError, ValueError):
                pass

        # name = text before the first qty/price token, letters only
        name = re.sub(r"[^A-Za-z &/-]", " ", s[:name_cut])
        rl.name = re.sub(r"\s+", " ", name).strip()

        if rl.name.lower() in _SKIP_WORDS:
            continue  # total / tax / footer line, not an item

        if rl.name:
            match = difflib.get_close_matches(rl.name.lower(), lc_names, n=1, cutoff=0.6)
            if match:
                rl.ingredient_name = match[0]
                rl.ingredient_id = ingredient_names[match[0]]

        if rl.total_price is not None or (rl.qty is not None and rl.unit is not None):
            out.append(rl)

    return out
