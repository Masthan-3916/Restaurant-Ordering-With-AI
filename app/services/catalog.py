from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import MenuItem
from app.repositories.contracts import CatalogRepositoryPort
from app.services.errors import AppError


class CatalogService:
    def __init__(self, db: Session, repository: CatalogRepositoryPort):
        self.db = db
        self.repository = repository

    def search(self, vegetarian: bool | None = None, category: str | None = None, max_price: Decimal | None = None, keyword: str | None = None) -> list[MenuItem]:
        return self.repository.search(vegetarian, category, max_price, keyword)

    def get(self, item_id: int, active_only: bool = True) -> MenuItem:
        item = self.repository.get(item_id)
        if item is None or (active_only and not item.active):
            raise AppError("not_found", "Menu item not found.", 404)
        return item

    def create(self, values: dict) -> MenuItem:
        item = MenuItem(**values)
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def update(self, item_id: int, values: dict) -> MenuItem:
        item = self.get(item_id, active_only=False)
        for key, value in values.items():
            setattr(item, key, value)
        self.db.commit()
        self.db.refresh(item)
        return item

    def disable(self, item_id: int) -> None:
        item = self.get(item_id, active_only=False)
        item.active = False
        self.db.commit()
