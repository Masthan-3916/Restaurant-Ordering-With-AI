from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MenuItem


class CatalogRepository:
    def __init__(self, db: Session):
        self.db = db

    def search(self, vegetarian: bool | None = None, category: str | None = None, max_price: Decimal | None = None, keyword: str | None = None) -> list[MenuItem]:
        query = select(MenuItem).where(MenuItem.active.is_(True))
        if vegetarian is not None:
            query = query.where(MenuItem.vegetarian.is_(vegetarian))
        if category:
            query = query.where(MenuItem.category.ilike(f"%{category}%"))
        if max_price is not None:
            query = query.where(MenuItem.price <= max_price)
        if keyword:
            query = query.where(MenuItem.title.ilike(f"%{keyword}%"))
        return list(self.db.scalars(query.order_by(MenuItem.title)))

    def get(self, item_id: int, lock: bool = False) -> MenuItem | None:
        query = select(MenuItem).where(MenuItem.id == item_id)
        if lock:
            query = query.with_for_update()
        return self.db.scalar(query)
