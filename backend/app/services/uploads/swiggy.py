"""Parses a Swiggy "past orders" CSV export.

The file is RAGGED: multi-item orders spill extra unquoted item columns past
the header width, so pandas.read_csv chokes on it. We read with csv.reader,
take the header's field count N and treat row[:N-1] as the fixed columns and
row[N-1:] as the item list (however many there are)."""
import csv
import decimal
import io
import re
from datetime import date, datetime

from app.core.clock import business_today, as_business_time
from app.services.uploads.channel_orders import ParsedOrder, ParsedItem, ParseError
from app.services.uploads.item_matching import MenuIndex

ITEM_RE = re.compile(r"(.+)_([^_]*)_(\d+)_([\d.]+)$")

REQUIRED_COLUMNS = [
    "Order ID", "Order-status", "Order-relay-time(ordered time)",
    "Item-SGST", "Item-CGST", "Item-IGST",
    "Restaurant Trade Discount", "Restaurant Coupon Discount Share",
]


def parse_swiggy_csv(data: bytes, menu_index: MenuIndex) -> tuple[list[ParsedOrder], list[ParseError], set[str], tuple[date, date], int]:
    """Returns (orders, parse_errors, unmapped_item_names, date_range,
    total_rows_seen). total_rows_seen - len(orders) is the count of rows
    that failed outright (upload_log.rows_failed)."""
    text = data.decode("utf-8-sig")
    all_rows = list(csv.reader(io.StringIO(text)))

    duration_range = None
    header_idx = None
    for i, row in enumerate(all_rows):
        if row and row[0].strip() == "Duration :" and len(row) >= 3:
            try:
                start = datetime.strptime(row[1].strip(), "%Y-%m-%d").date()
                end = datetime.strptime(row[2].strip(), "%Y-%m-%d").date()
                duration_range = (start, end)
            except ValueError:
                pass
        if row and row[0].strip() == "Order ID":
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find header row starting with 'Order ID'")

    header = all_rows[header_idx]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"Missing expected column(s): {', '.join(missing)}")
    col = {name: i for i, name in enumerate(header)}
    n_fixed = len(header) - 1  # last header column is the (possibly-ragged) item list

    orders: list[ParsedOrder] = []
    errors: list[ParseError] = []
    unmapped: set[str] = set()
    total_rows_seen = 0

    for line_no, row in enumerate(all_rows[header_idx + 1:], start=header_idx + 2):
        if not row or not row[0].strip():
            continue
        total_rows_seen += 1
        ext_id = row[col["Order ID"]].strip()
        try:
            fixed = row[:n_fixed]
            item_tokens = row[n_fixed:]

            status_raw = fixed[col["Order-status"]].strip()
            status = "delivered" if status_raw.lower() == "delivered" else "cancelled"
            placed_at = as_business_time(datetime.strptime(fixed[col["Order-relay-time(ordered time)"]].strip(), "%Y-%m-%d %H:%M:%S"))

            sgst = decimal.Decimal(fixed[col["Item-SGST"]] or "0")
            cgst = decimal.Decimal(fixed[col["Item-CGST"]] or "0")
            igst = decimal.Decimal(fixed[col["Item-IGST"]] or "0")
            net = (sgst + cgst + igst) / decimal.Decimal("0.05")

            trade_disc = decimal.Decimal(fixed[col["Restaurant Trade Discount"]] or "0")
            coupon_disc = decimal.Decimal(fixed[col["Restaurant Coupon Discount Share"]] or "0")
            restaurant_discount = trade_disc + coupon_disc
            gross = net + restaurant_discount

            items: list[ParsedItem] = []
            for tok in item_tokens:
                tok = tok.strip()
                if not tok:
                    continue
                tok = tok.split("+", 1)[0]
                m = ITEM_RE.match(tok)
                if not m:
                    errors.append(ParseError(line_no, f"Unparseable item token: {tok!r}"))
                    continue
                name, _reward, qty_str, lineprice_str = m.groups()
                name = name.strip()
                qty = int(qty_str)
                unit_price = (decimal.Decimal(lineprice_str) / qty).quantize(decimal.Decimal("0.01"))
                mid = menu_index.match(name)
                if mid is None:
                    unmapped.add(name)
                items.append(ParsedItem(raw_name=name, menu_item_id=mid, quantity=qty, unit_price=unit_price))

            orders.append(ParsedOrder(
                external_order_id=ext_id,
                placed_at=placed_at,
                status=status,
                net=net.quantize(decimal.Decimal("0.01")),
                gross=gross.quantize(decimal.Decimal("0.01")),
                restaurant_discount=restaurant_discount.quantize(decimal.Decimal("0.01")),
                platform_discount=decimal.Decimal("0"),
                items=items,
            ))
        except Exception as exc:
            errors.append(ParseError(line_no, f"Order {ext_id or '?'}: {exc}"))

    if duration_range is None:
        dates = [o.placed_at.date() for o in orders]
        duration_range = (min(dates), max(dates)) if dates else (business_today(), business_today())

    return orders, errors, unmapped, duration_range, total_rows_seen
