import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.order import Order, OrderItem
from app.schemas import OrderCreate, OrderRead
from app.services.order_derived_stock import apply_order_derived_deductions

logger = logging.getLogger("orders")

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/", response_model=list[OrderRead])
def list_orders(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Order).offset(skip).limit(limit).all()


@router.post("/", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    total = sum(i.unit_price * i.quantity for i in payload.items)
    order = Order(
        customer_id=payload.customer_id,
        status=payload.status,
        total_amount=total,
    )
    db.add(order)
    db.flush()  # get order.id before inserting items

    for item_data in payload.items:
        order_item = OrderItem(order_id=order.id, **item_data.model_dump())
        db.add(order_item)

    try:
        with db.begin_nested():
            apply_order_derived_deductions(db)
    except Exception:
        # Experimental comparison model -- must never block order placement.
        logger.exception("order-derived deduction pass failed")

    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}", response_model=OrderRead)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.patch("/{order_id}/status", response_model=OrderRead)
def update_order_status(order_id: int, new_status: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = new_status
    db.commit()
    db.refresh(order)
    return order
