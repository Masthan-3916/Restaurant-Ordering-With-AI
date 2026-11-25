from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import order_service
from app.models import User
from app.api.orders import order_response
from app.schemas.api import OrderResponse
from app.security.auth import admin_user
from app.services.orders import OrderService

router = APIRouter(prefix="/api/admin/orders", tags=["admin"])


class StatusInput(BaseModel):
    status: str = Field(pattern="^(PREPARING|READY|COMPLETED|CANCELLED)$")


@router.get("", response_model=list[OrderResponse])
def list_all_orders(
    service: Annotated[OrderService, Depends(order_service)],
    _: Annotated[User, Depends(admin_user)],
):
    return [order_response(order) for order in service.list_all()]


@router.patch("/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: int,
    data: StatusInput,
    service: Annotated[OrderService, Depends(order_service)],
    _: Annotated[User, Depends(admin_user)],
):
    return order_response(service.set_status(order_id, data.status))
