"""Bill drafts require explicit buyer review before submission."""
import io
import re
from app.services.rush_scan import read_photo


def read_bill(data, api_key):
    if not data.startswith(b"%PDF"):
        return read_photo(data, api_key)[:60000]
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 15:
            raise ValueError("Use a PDF with at most 15 pages.")
        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if not page_text.strip():
                images = list(page.images)
                if not images or len(images) > 4:
                    raise ValueError("Take a photo of this PDF page to read it.")
                page_text = "\n".join(read_photo(image.data, api_key) for image in images)
            pages.append(page_text)
        content = "\n".join(pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Could not read this PDF. Try photos of its pages instead.") from exc
    if not content.strip():
        raise ValueError("This PDF contains scanned images. Take photos of its pages to use phone OCR.")
    return content[:60000]


def parse_bill(content, ingredients):
    def key(value):
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    names = {}
    for item in ingredients:
        names.setdefault(key(item.name), []).append(item)
    lines, unparsed = [], []
    pattern = re.compile(r"^(.+?)\s+(\d+(?:\.\d+)?)\s*(kg|g|ml|l|pcs)\s+(?:.*?\s)?(?:Rs\.?|₹)?\s*(\d[\d,]*(?:\.\d{1,2})?)$", re.I)
    for raw in content.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        match = pattern.match(raw)
        if not match:
            unparsed.append(raw)
            continue
        name, qty, unit, amount = match.groups()
        candidates = names.get(key(name), [])
        item = candidates[0] if len(candidates) == 1 else None
        lines.append({"name": item.name if item else name, "qty": float(qty), "unit": unit.lower(),
                      "amount": float(amount.replace(",", ""))})
    return {"text": content, "lines": lines, "unparsed": unparsed}
