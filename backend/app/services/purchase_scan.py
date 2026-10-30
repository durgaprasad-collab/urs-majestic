"""Bill drafts require explicit buyer review before submission."""
import difflib
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


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_HSN = re.compile(r"\(?\bHSN\b[-\s:]*\d+\)?", re.I)
_CELL = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")
_PACK = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|gm|ml|ltr|l)\b", re.I)


def _pack_total(description, count):
    """'Capsicum 250 g' x 4 -> (1.0, 'kg'). Ledger quantities are by weight/volume,
    so a bill's pack count is multiplied by the pack size in its description.
    Returns None when the description has no size."""
    sizes = _PACK.findall(description)
    if not sizes:
        return None
    size, unit = sizes[-1]
    unit = {"gm": "g", "ltr": "l"}.get(unit.lower(), unit.lower())
    total = round(float(size) * count, 3)
    if unit == "g" and total >= 1000:
        total, unit = round(total / 1000, 3), "kg"
    elif unit == "ml" and total >= 1000:
        total, unit = round(total / 1000, 3), "l"
    return total, unit


def _gst_invoice_rows(rows):
    """Quick-commerce GST invoices (Blinkit etc.) print each table cell on its own
    line. An item is its description (ending '(HSN-nnnn)') followed by 11 numbers:
    MRP, discount, qty, taxable, CGST %, CGST, SGST %, SGST, cess %, extra cess,
    total. A row is accepted only when taxable + taxes adds up to its total, so a
    misread cell is left for the buyer instead of being guessed.
    Returns ([(description, qty, total)], {indexes consumed})."""
    found, used = [], set()
    for i, row in enumerate(rows):
        if not _HSN.search(row):
            continue
        desc, j = [], i
        while j >= 0 and re.search(r"[A-Za-z]", rows[j]) and not re.match(r"^(item description|total)\b", rows[j], re.I):
            desc.insert(0, rows[j])
            j -= 1
        text = re.sub(r"^(?:\d[\d,.]*\s+)+", "", _HSN.sub("", " ".join(desc))).strip(" -")
        nums, k = [], i + 1
        while k < len(rows) and _NUM.fullmatch(rows[k]):
            nums.append(float(rows[k].replace(",", "")))
            k += 1
        if not text or len(nums) < 11:
            continue
        mrp, disc, qty, taxable, _, cgst, _, sgst, _, cess, total = nums[:11]
        if qty <= 0 or abs(taxable + cgst + sgst + cess - total) > 0.06:
            continue
        found.append((text, qty, total))
        used.update(range(j + 1, i + 1))
        used.update(range(i + 1, i + 12))
    # Tables that print a bare HSN code line after the description and then 13 cells:
    # qty, unit price, unit taxable, gross, discount, other charges, taxable,
    # CGST %, CGST, SGST %, SGST, cess, total (BigBasket). Same sum check.
    for i, row in enumerate(rows):
        if i in used or not re.fullmatch(r"\d{4,8}", row):
            continue
        cells, k = [], i + 1
        while k < len(rows) and _CELL.fullmatch(rows[k]):
            cells.append(float(rows[k].replace(",", "").rstrip("%")))
            k += 1
        if len(cells) < 13:
            continue
        qty, taxable, cgst, sgst, cess, total = cells[0], cells[6], cells[8], cells[10], cells[11], cells[12]
        if qty <= 0 or abs(taxable + cgst + sgst + cess - total) > 0.06:
            continue
        desc, j = [], i - 1
        while j >= 0 and re.search(r"[A-Za-z]", rows[j]) and not re.match(r"^(item description|total|si|no\.?)\b", rows[j], re.I):
            desc.insert(0, rows[j])
            j -= 1
        text = " ".join(desc).strip(" -")
        if not text:
            continue
        found.append((text, qty, total))
        used.update(range(j + 1, i + 14))
    # "Delivery and other charges" row: '-' cells, then taxable, CGST %, CGST,
    # SGST %, SGST, cess %, cess, total. Logged as one charge of the total incl. GST.
    for i, row in enumerate(rows):
        if i in used or not re.match(r"^(delivery|shipping|handling)\b.*\bcharges?\b", row, re.I):
            continue
        k = i + 1
        while k < len(rows) and rows[k] in ("-", ""):
            k += 1
        nums = []
        while k < len(rows) and _NUM.fullmatch(rows[k]):
            nums.append(float(rows[k].replace(",", "")))
            k += 1
        if len(nums) < 8:
            continue
        taxable, _, cgst, _, sgst, _, cess, total = nums[:8]
        if total <= 0 or abs(taxable + cgst + sgst + cess - total) > 0.06:
            continue
        found.append((row, 1.0, total))
        used.update(range(i, k))
        while i > 0 and rows[i - 1] in ("-", ""):
            i -= 1
            used.add(i)
    return found, used


def _bill_header(content):
    out = {}
    m = re.search(r"Invoice Date\W*(?:Invoice Date\W*)*(\d{4}-\d{2}-\d{2})", content, re.I)
    if m:
        out["date"] = m.group(1)
    m = re.search(r"Invoice (?:Number|No\.?)\W*(?:Invoice (?:Number|No\.?)\W*)*(?=[A-Z0-9/-]*\d)([A-Z0-9][A-Z0-9/-]{5,})", content, re.I)
    if m:
        out["bill_ref"] = m.group(1)
    return out


def parse_bill(content, ingredients):
    def key(value):
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    names = {}
    for item in ingredients:
        names.setdefault(key(item.name), []).append(item)
    lines, unparsed = [], []
    all_rows = [r.strip().strip("|").strip() for r in content.splitlines()]
    gst_rows, gst_used = _gst_invoice_rows(all_rows)
    for text, qty, total in gst_rows:
        k = key(text)
        # An item whose name sits inside the printed description ("Sunflower Oil" in
        # "Be Rite Refined Sunflower Oil (Pouch)") is chosen only when exactly one does.
        hits = {n: c[0] for n, c in names.items() if n and len(c) == 1 and f" {n} " in f" {k} "}
        # Several hits ("Salt" and "Iodised Salt"): the longest wins only if it contains the rest.
        longest = max(hits, key=len) if hits else None
        item = hits[longest] if longest and all(n in longest for n in hits) else None
        if re.match(r"^(delivery|shipping|handling)\b", text, re.I):
            # The ledger's one item for these charges, quantity 1.
            item = (names.get("vendor delivery charge") or [None])[0] if len(names.get("vendor delivery charge", [])) == 1 else None
        # A size in the description gives weight/volume (4 x 250 g = 1 kg); otherwise the
        # bill gives pieces only and the unit is left as pcs for the buyer to confirm.
        pack = None if re.match(r"^(delivery|shipping|handling)\b", text, re.I) else _pack_total(text, qty)
        lines.append({"name": item.name if item else text, "qty": pack[0] if pack else qty,
                      "unit": pack[1] if pack else "pcs", "amount": total})
    # Unit may touch the quantity ("3kg"), "pes"/"pc" are OCR/shorthand for pcs, and
    # handwriting adds trailing marks ("72-", "72/-"). A line with no unit stays
    # unparsed on purpose: the quantity could be kg or pieces.
    pattern = re.compile(r"^(.+?)\s+(\d+(?:\.\d+)?)\s*(kg|g|ml|l|pcs|pc|pes)\b\.?\s+(?:.*?\s)?(?:Rs\.?|₹)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:/-|-|=|/)?\s*$", re.I)
    for idx, raw in enumerate(all_rows):
        if not raw or idx in gst_used:
            continue
        match = pattern.match(raw)
        if not match:
            unparsed.append(raw)
            continue
        name, qty, unit, amount = match.groups()
        unit = {"pc": "pcs", "pes": "pcs"}.get(unit.lower(), unit.lower())
        candidates = names.get(key(name), [])
        if not candidates:
            # Close spelling ("Tomatoe", "Podkins") picks the item only when one is clearly nearest.
            close = difflib.get_close_matches(key(name), list(names), n=2, cutoff=0.8)
            if len(close) == 1:
                candidates = names[close[0]]
        item = candidates[0] if len(candidates) == 1 else None
        lines.append({"name": item.name if item else name, "qty": float(qty), "unit": unit,
                      "amount": float(amount.replace(",", ""))})
    return {"text": content, "lines": lines, "unparsed": unparsed, **_bill_header(content)}
