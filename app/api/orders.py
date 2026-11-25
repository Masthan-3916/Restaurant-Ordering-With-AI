from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.api.dependencies import order_service
from app.models import Order, User
from app.schemas.api import OrderInput, OrderLineResponse, OrderResponse
from app.security.auth import current_user
from app.services.orders import OrderService

router = APIRouter(prefix="/api/orders", tags=["orders"])


def order_response(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        status=order.status,
        total=order.total,
        created_at=order.created_at,
        lines=[
            OrderLineResponse(item_id=line.item_id, title=line.item.title, quantity=line.quantity, unit_price=line.unit_price)
            for line in order.lines
        ],
    )


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    data: OrderInput,
    service: Annotated[OrderService, Depends(order_service)],
    user: Annotated[User, Depends(current_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=100)] = None,
):
    order = service.create(user, [line.model_dump() for line in data.lines], idempotency_key)
    return order_response(order)


@router.get("", response_model=list[OrderResponse])
def list_orders(service: Annotated[OrderService, Depends(order_service)], user: Annotated[User, Depends(current_user)]):
    return [order_response(order) for order in service.list_mine(user)]


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, service: Annotated[OrderService, Depends(order_service)], user: Annotated[User, Depends(current_user)]):
    return order_response(service.get(user, order_id))


@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(order_id: int, service: Annotated[OrderService, Depends(order_service)], user: Annotated[User, Depends(current_user)]):
    return order_response(service.cancel(user, order_id))
