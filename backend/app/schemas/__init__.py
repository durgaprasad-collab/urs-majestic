import decimal
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.models.order import OrderStatus


# ── Customer ─────────────────────────────────────────────────────────────────

class CustomerBase(BaseModel):
    name: str
    mobile: str
    email: Optional[str] = None
    area: Optional[str] = None


class CustomerCreate(CustomerBase):
    pass


class CustomerRead(CustomerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


# ── MenuItem ──────────────────────────────────────────────────────────────────

class PosAliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pos_name: str


class MenuItemBase(BaseModel):
    name: str
    category: str
    price: decimal.Decimal
    is_active: bool = True
    is_food: bool = True


class MenuItemCreate(MenuItemBase):
    pass


class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    price: Optional[decimal.Decimal] = None
    is_active: Optional[bool] = None
    is_food: Optional[bool] = None


class MenuItemRead(MenuItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pos_aliases: list[PosAliasRead] = []


# ── Order ─────────────────────────────────────────────────────────────────────

class OrderItemBase(BaseModel):
    menu_item_id: int
    quantity: int
    unit_price: decimal.Decimal


class OrderItemCreate(OrderItemBase):
    pass


class OrderItemRead(OrderItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int


class OrderBase(BaseModel):
    customer_id: Optional[int] = None
    status: OrderStatus = OrderStatus.pending


class OrderCreate(OrderBase):
    items: list[OrderItemCreate]


class OrderRead(OrderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    total_amount: decimal.Decimal
    created_at: datetime
    items: list[OrderItemRead] = []
