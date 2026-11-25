from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ItemInput(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    category: str = Field(min_length=2, max_length=80)
    vegetarian: bool
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    capacity: int = Field(ge=0)


class ItemResponse(ItemInput):
    id: int
    active: bool
    model_config = ConfigDict(from_attributes=True)


class OrderLineInput(BaseModel):
    item_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=10)


class OrderInput(BaseModel):
    lines: list[OrderLineInput] = Field(min_length=1, max_length=20)


class OrderLineResponse(BaseModel):
    item_id: int
    title: str
    quantity: int
    unit_price: Decimal


class OrderResponse(BaseModel):
    id: int
    status: str
    total: Decimal
    created_at: datetime
    lines: list[OrderLineResponse]


class SessionInput(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=160)


class SessionResponse(BaseModel):
    id: int
    title: str
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    user_message: dict
    assistant_message: dict
    mode: str
    citations: list[dict] = []
    pending_action: dict | None = None


class MessageResponse(BaseModel):
    id: int
    seq_no: int
    role: str
    content: str
    metadata: dict
    created_at: datetime


class ConfirmInput(BaseModel):
    confirm: bool


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
