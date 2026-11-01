"""Zomato's finished day, from the add-on, into the same tables the Zomato order-history CSV feeds.

The add-on reads each order's own details data. Checked against 93 orders already uploaded
from the CSV (net amount and items all equal, 92 of 93 statuses):
  net    = bill subtotal minus the restaurant's discount;
  the restaurant's discount = the order's per-dish "x% OFF" discounts (or, when it has none, its
           order-level percentage discount) plus its order-level flat promo discount. Zomato-funded
           offers (new-user codes, "GETOFF40ON349" style flat offers) are not the restaurant's;
  status = delivered only when Zomato says DELIVERED, anything else counts as cancelled."""
import datetime
import decimal

from app.core.clock import business_date_of, business_today, business_tz
from app.services.uploads.channel_orders import ParsedItem, ParsedOrder
from app.services.uploads.item_matching import MenuIndex

D = decimal.Decimal
CENT = D("0.01")
MAX_ORDERS = 400


def _money(v, what: str) -> decimal.Decimal:
    try:
        d = D(str(v if v not in (None, "") else 0))
    except decimal.InvalidOperation:
        raise ValueError(f"bad {what}") from None
    if not d.is_finite() or d < 0:
        raise ValueError(f"bad {what}")
    return d


def orders_from_feed(day: str, raw, menu_index: MenuIndex) -> tuple[list[ParsedOrder], set[str]]:
    """[{id, state, created 'YYYY-MM-DDTHH:MM:SSZ', subtotal, salt, pct, abs, items [{name, qty}]}] for one
    finished calendar day -> parsed orders + unmatched item names."""
    try:
        the_day = datetime.date.fromisoformat(str(day))
    except ValueError:
        raise ValueError("bad day") from None
    if the_day >= business_today():
        raise ValueError("That day has not finished yet")
    if not isinstance(raw, list):
        raise ValueError("orders must be a list")
    if len(raw) > MAX_ORDERS:
        raise ValueError("too many orders")
    orders, unmapped = [], set()
    for o in raw:
        oid = str(o.get("id") or "").strip()
        if not oid.isdigit():
            raise ValueError(f"bad order id {oid[:20]!r}")
        try:
            created = datetime.datetime.fromisoformat(str(o.get("created") or "").replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"bad time for order {oid}") from None
        if created.tzinfo is None:
            raise ValueError(f"order {oid} time has no zone")
        placed = created.astimezone(business_tz())
        if business_date_of(placed) != the_day:
            continue  # the history list spans two calendar days; keep only this one
        subtotal = _money(o.get("subtotal"), "subtotal")
        restaurant_discount = (_money(o.get("salt"), "discount") or _money(o.get("pct"), "discount")) + _money(o.get("abs"), "discount")
        items = []
        for it in o.get("items") or []:
            name, qty = str(it.get("name") or "").strip(), int(it.get("qty") or 0)
            if not name or qty <= 0:
                continue
            mid = menu_index.match(name)
            if mid is None:
                unmapped.add(name)
            items.append(ParsedItem(raw_name=name, menu_item_id=mid, quantity=qty, unit_price=None))
        orders.append(ParsedOrder(
            external_order_id=oid, placed_at=placed,
            status="delivered" if str(o.get("state") or "").strip().upper() == "DELIVERED" else "cancelled",
            net=(subtotal - restaurant_discount).quantize(CENT), gross=subtotal.quantize(CENT),
            restaurant_discount=restaurant_discount.quantize(CENT), platform_discount=D("0"), items=items))
    return orders, unmapped
