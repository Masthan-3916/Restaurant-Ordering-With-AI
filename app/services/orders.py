from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MenuItem, Order, OrderLine, User
from app.repositories.contracts import CatalogRepositoryPort, OrderRepositoryPort
from app.services.errors import AppError


class OrderService:
    def __init__(self, db: Session, catalog: CatalogRepositoryPort, orders: OrderRepositoryPort):
        self.db = db
        self.catalog = catalog
        self.orders = orders

    def create(self, user: User, lines: list[dict], idempotency_key: str | None = None) -> Order:
        key = idempotency_key or str(uuid4())
        quantities: dict[int, int] = {}
        for line in lines:
            quantities[line["item_id"]] = quantities.get(line["item_id"], 0) + line["quantity"]
        if any(quantity > 10 for quantity in quantities.values()):
            raise AppError("invalid_quantity", "A menu item may be ordered in quantities from 1 to 10.", 422)

        existing = self.db.scalar(select(Order).where(Order.user_id == user.id, Order.idempotency_key == key))
        if existing:
            return self._replay(existing.id, quantities)
        try:
            items: dict[int, MenuItem] = {}
            for item_id, quantity in quantities.items():
                item = self.catalog.get(item_id, lock=True)
                if not item.active or item.capacity < quantity:
                    raise AppError("insufficient_capacity", f"{item.title} is no longer available in that quantity.", 409)
                items[item_id] = item
            order = Order(user_id=user.id, status="CONFIRMED", total=Decimal("0"), idempotency_key=key)
            for item_id, quantity in quantities.items():
                item = items[item_id]
                item.capacity -= quantity
                order.lines.append(OrderLine(item_id=item.id, quantity=quantity, unit_price=item.price))
                order.total += item.price * quantity
            self.db.add(order)
            self.db.commit()
            self.db.refresh(order)
            return self.orders.get(order.id)
        except IntegrityError:
            self.db.rollback()
            existing = self.db.scalar(select(Order).where(Order.user_id == user.id, Order.idempotency_key == key))
            if existing:
                return self._replay(existing.id, quantities)
            raise
        except Exception:
            self.db.rollback()
            raise

    def _replay(self, order_id: int, quantities: dict[int, int]) -> Order:
        order = self.orders.get(order_id)
        existing_lines = {line.item_id: line.quantity for line in order.lines}
        if existing_lines != quantities:
            raise AppError("idempotency_conflict", "This Idempotency-Key was already used for a different order.", 409)
        return order

    def list_mine(self, user: User) -> list[Order]:
        return self.orders.list_for_user(user.id)

    def list_all(self) -> list[Order]:
        return self.orders.list_all()

    def get(self, user: User, order_id: int) -> Order:
        order = self.orders.get(order_id)
        if order is None or (order.user_id != user.id and user.role != "admin"):
            raise AppError("not_found", "Order not found.", 404)
        return order

    def cancel(self, user: User, order_id: int) -> Order:
        try:
            order = self.orders.get(order_id, lock=True)
            if order is None or order.user_id != user.id:
                raise AppError("not_found", "Order not found.", 404)
            if order.status != "CONFIRMED":
                raise AppError("invalid_transition", "Only orders not yet in preparation can be cancelled.", 409)
            for line in order.lines:
                item = self.catalog.get(line.item_id, lock=True)
                item.capacity += line.quantity
            order.status = "CANCELLED"
            self.db.commit()
            return self.orders.get(order.id)
        except Exception:
            self.db.rollback()
            raise

    def set_status(self, order_id: int, status: str) -> Order:
        allowed = {"CONFIRMED": {"PREPARING", "CANCELLED"}, "PREPARING": {"READY"}, "READY": {"COMPLETED"}}
        try:
            order = self.orders.get(order_id, lock=True)
            if order is None:
                raise AppError("not_found", "Order not found.", 404)
            if status not in allowed.get(order.status, set()):
                raise AppError("invalid_transition", f"Cannot change order status from {order.status} to {status}.", 409)
            if status == "CANCELLED":
                for line in order.lines:
                    item = self.catalog.get(line.item_id, lock=True)
                    item.capacity += line.quantity
            order.status = status
            self.db.commit()
            return self.orders.get(order.id)
        except Exception:
            self.db.rollback()
            raise
