from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.repositories.catalog import CatalogRepository
from app.repositories.orders import OrderRepository
from app.services.catalog import CatalogService
from app.services.orders import OrderService


def catalog_service(db: Annotated[Session, Depends(get_db)]) -> CatalogService:
    return CatalogService(db, CatalogRepository(db))


def order_service(db: Annotated[Session, Depends(get_db)]) -> OrderService:
    return OrderService(db, CatalogRepository(db), OrderRepository(db))
