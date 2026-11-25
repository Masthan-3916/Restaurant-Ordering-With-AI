from decimal import Decimal
from typing import Protocol

from app.models import MenuItem, Order


class CatalogRepositoryPort(Protocol):
    def search(
        self,
        vegetarian: bool | None = None,
        category: str | None = None,
        max_price: Decimal | None = None,
        keyword: str | None = None,
    ) -> list[MenuItem]: ...

    def get(self, item_id: int, lock: bool = False) -> MenuItem | None: ...


class OrderRepositoryPort(Protocol):
    def get(self, order_id: int, lock: bool = False) -> Order | None: ...

    def list_for_user(self, user_id: int) -> list[Order]: ...

    def list_all(self) -> list[Order]: ...
