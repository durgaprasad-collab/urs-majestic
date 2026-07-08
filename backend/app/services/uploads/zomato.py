"""Parses a Zomato partner-dashboard "Order history" CSV export."""
import csv
import decimal
import io
import re
from datetime import date, datetime

from app.services.uploads.channel_orders import ParsedOrder, ParsedItem, ParseError
from app.services.uploads.item_matching import MenuIndex

ITEM_SPLIT_RE = re.compile(r",\s*(?=\d+\s*x\s)")
ITEM_TOKEN_RE = re.compile(r"(\d+)\s*x\s*(.+)")

REQUIRED_COLUMNS = [
    "Order ID", "Order Placed At", "Order Status", "Bill subtotal",
    "Restaurant discount (Promo)", "Restaurant discount (Flat offs, Freebies & others)",
    "Gold discount", "Brand pack discount", "Items in order",
]


def parse_zomato_csv(data: bytes, menu_index: MenuIndex) -> tuple[list[ParsedOrder], list[ParseError], set[str]]:
    """Returns (orders, parse_errors, unmapped_item_names)."""
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"Missing expected column(s): {', '.join(missing)}")

    orders: list[ParsedOrder] = []
    errors: list[ParseError] = []
    unmapped: set[str] = set()

    for line_no, row in enumerate(reader, start=2):  # header is line 1
        ext_id = (row.get("Order ID") or "").strip()
        if not ext_id:
            continue
        try:
            placed_at = datetime.strptime(row["Order Placed At"].strip(), "%I:%M %p, %B %d %Y")
            status = "delivered" if row["Order Status"].strip() == "Delivered" else "cancelled"
            subtotal = decimal.Decimal(row["Bill subtotal"] or "0")
            promo = decimal.Decimal(row["Restaurant discount (Promo)"] or "0")
            flat = decimal.Decimal(row["Restaurant discount (Flat offs, Freebies & others)"] or "0")
            gold = decimal.Decimal(row["Gold discount"] or "0")
            brand_pack = decimal.Decimal(row["Brand pack discount"] or "0")
            restaurant_discount = promo + flat + gold
            net = subtotal - restaurant_discount

            items: list[ParsedItem] = []
            for tok in ITEM_SPLIT_RE.split(row["Items in order"] or ""):
                tok = tok.strip()
                if not tok:
                    continue
                m = ITEM_TOKEN_RE.match(tok)
                if not m:
                    errors.append(ParseError(line_no, f"Unparseable item token: {tok!r}"))
                    continue
                qty, name = int(m.group(1)), m.group(2).strip()
                mid = menu_index.match(name)
                if mid is None:
                    unmapped.add(name)
                items.append(ParsedItem(raw_name=name, menu_item_id=mid, quantity=qty, unit_price=None))

            orders.append(ParsedOrder(
                external_order_id=ext_id,
                placed_at=placed_at,
                status=status,
                net=net,
                gross=subtotal,
                restaurant_discount=restaurant_discount,
                platform_discount=brand_pack,
                items=items,
            ))
        except Exception as exc:
            errors.append(ParseError(line_no, f"Order {ext_id or '?'}: {exc}"))

    return orders, errors, unmapped


def date_range_from_filename(filename: str, orders: list[ParsedOrder]) -> tuple[date, date]:
    """Zomato's export has no metadata date-range row; the filename encodes
    it (order_history_YYYYMMDD_YYYYMMDD.csv). Falls back to the min/max
    order date actually present if the filename doesn't match."""
    m = re.search(r"(\d{8})_(\d{8})", filename or "")
    if m:
        start = datetime.strptime(m.group(1), "%Y%m%d").date()
        end = datetime.strptime(m.group(2), "%Y%m%d").date()
        return start, end
    dates = [o.placed_at.date() for o in orders]
    if not dates:
        today = date.today()
        return today, today
    return min(dates), max(dates)
