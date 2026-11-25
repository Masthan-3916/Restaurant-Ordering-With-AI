from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Order, OrderLine


class OrderRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, order_id: int, lock: bool = False) -> Order | None:
        query = select(Order).options(selectinload(Order.lines).selectinload(OrderLine.item)).where(Order.id == order_id)
        if lock:
            query = query.with_for_update()
        return self.db.scalar(query)

    def list_for_user(self, user_id: int) -> list[Order]:
        query = select(Order).options(selectinload(Order.lines).selectinload(OrderLine.item)).where(Order.user_id == user_id).order_by(Order.created_at.desc())
        return list(self.db.scalars(query))

    def list_all(self) -> list[Order]:
        query = select(Order).options(selectinload(Order.lines).selectinload(OrderLine.item)).order_by(Order.created_at.desc())
        return list(self.db.scalars(query))
