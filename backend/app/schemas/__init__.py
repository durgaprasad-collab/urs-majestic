import decimal
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.models.order import OrderStatus
from app.models.requisition import RequisitionStatus, RequisitionUrgency


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


# ── Auth (mobile) ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    name: str
    is_owner: bool


# ── Requisition ───────────────────────────────────────────────────────────────

class RequisitionCreate(BaseModel):
    item_name: str
    quantity: Optional[decimal.Decimal] = None
    unit: Optional[str] = None
    urgency: RequisitionUrgency = RequisitionUrgency.normal
    note: Optional[str] = None


class RequisitionDecision(BaseModel):
    approve: bool
    decision_note: Optional[str] = None


class RequisitionUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class RequisitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_name: str
    ingredient_id: Optional[int] = None
    quantity: Optional[decimal.Decimal] = None
    unit: Optional[str] = None
    urgency: RequisitionUrgency
    note: Optional[str] = None
    status: RequisitionStatus
    requested_by: RequisitionUserRead
    decided_by: Optional[RequisitionUserRead] = None
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    matched_purchase_id: Optional[int] = None
    created_at: datetime


# ── Stock (mobile) ────────────────────────────────────────────────────────────

class LowStockItem(BaseModel):
    ingredient_id: int
    name: str
    category: Optional[str] = None
    unit: str
    on_hand_qty: Optional[decimal.Decimal] = None
    cover_days: Optional[float] = None
    counted_at: Optional[datetime] = None


class StockCountCreate(BaseModel):
    ingredient_id: int
    qty: decimal.Decimal
    unit: str


class DeviceTokenRegister(BaseModel):
    token: str
    platform: str = "android"


# ── Ingredient (mobile picker) ─────────────────────────────────────────────────

class IngredientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    unit: str
    category: Optional[str] = None
