"""Seed the confirmed catering order for Deepak (25 Chapati & Channa sets,
15 Chilli Garlic Fried Rice, 10 Gobi Manchurian -- delivery 2026-08-28).

Idempotent: matched on (customer_phone, delivery_date, delivery_time), so
re-running is a no-op once seeded rather than creating a duplicate booking.

Run from backend/:  D:\\URS_Majestic\\.venv\\Scripts\\python.exe -m scripts.seed_catering_order_deepak
"""
from datetime import date, time
from decimal import Decimal

from app.core.database import SessionLocal
from app.models.catering_order import CateringOrder, CateringOrderItem, CateringPaymentStatus, CateringOrderStatus

_CUSTOMER_NAME = "Deepak"
_CUSTOMER_PHONE = "9092550761"
_ORDER_TAKEN_DATE = date(2026, 8, 28)
_DELIVERY_DATE = date(2026, 8, 28)
_DELIVERY_TIME = time(17, 0)
# Pickup order -- no delivery destination. (The address originally supplied
# alongside this order was URS Majestic's own shop address, which would have
# been wrong here regardless, since this field is for the customer's
# delivery location, not ours.)
_DELIVERY_ADDRESS = None

_ITEMS = [
    # (item_name, quantity, unit, amount)
    ("Chapati & Channa (Set)", 25, "sets", Decimal("2225.00")),
    ("Chilli Garlic Fried Rice", 15, "portions", Decimal("2340.00")),
    ("Gobi Manchurian", 10, "portions", Decimal("1350.00")),
]


def main() -> None:
    db = SessionLocal()
    try:
        existing = (
            db.query(CateringOrder)
            .filter(
                CateringOrder.customer_phone == _CUSTOMER_PHONE,
                CateringOrder.delivery_date == _DELIVERY_DATE,
                CateringOrder.delivery_time == _DELIVERY_TIME,
            )
            .first()
        )
        if existing:
            print(f"catering order for {_CUSTOMER_NAME} on {_DELIVERY_DATE} {_DELIVERY_TIME} already seeded "
                  f"(id={existing.id}) -- skipping")
            return

        order = CateringOrder(
            customer_name=_CUSTOMER_NAME,
            customer_phone=_CUSTOMER_PHONE,
            order_taken_date=_ORDER_TAKEN_DATE,
            delivery_date=_DELIVERY_DATE,
            delivery_time=_DELIVERY_TIME,
            delivery_address=_DELIVERY_ADDRESS,
            subtotal=Decimal("5915.00"),
            discount=Decimal("415.00"),
            total_amount=Decimal("5500.00"),
            advance_paid=Decimal("2500.00"),
            balance_due=Decimal("3000.00"),
            payment_status=CateringPaymentStatus.partial,
            status=CateringOrderStatus.confirmed,
        )
        db.add(order)
        db.flush()  # get order.id before inserting items

        for item_name, quantity, unit, amount in _ITEMS:
            db.add(CateringOrderItem(
                catering_order_id=order.id, item_name=item_name,
                quantity=quantity, unit=unit, amount=amount,
            ))

        db.commit()
        print(f"seeded catering order id={order.id} for {_CUSTOMER_NAME} with {len(_ITEMS)} item(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
