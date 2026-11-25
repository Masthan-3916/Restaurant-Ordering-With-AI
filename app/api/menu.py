from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from app.api.dependencies import catalog_service
from app.models import User
from app.schemas.api import ItemInput, ItemResponse
from app.security.auth import admin_user
from app.services.catalog import CatalogService

router = APIRouter(prefix="/api/menu_items", tags=["menu"])


@router.get("", response_model=list[ItemResponse])
def list_menu(
    service: Annotated[CatalogService, Depends(catalog_service)],
    vegetarian: bool | None = None,
    category: str | None = None,
    max_price: Decimal | None = Query(default=None, gt=0),
    keyword: str | None = None,
):
    return service.search(vegetarian, category, max_price, keyword)


@router.get("/{item_id}", response_model=ItemResponse)
def get_menu_item(item_id: int, service: Annotated[CatalogService, Depends(catalog_service)]):
    return service.get(item_id)


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_menu_item(
    data: ItemInput,
    service: Annotated[CatalogService, Depends(catalog_service)],
    _: Annotated[User, Depends(admin_user)],
):
    return service.create(data.model_dump())


@router.put("/{item_id}", response_model=ItemResponse)
def update_menu_item(
    item_id: int,
    data: ItemInput,
    service: Annotated[CatalogService, Depends(catalog_service)],
    _: Annotated[User, Depends(admin_user)],
):
    return service.update(item_id, data.model_dump())


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_menu_item(
    item_id: int,
    service: Annotated[CatalogService, Depends(catalog_service)],
    _: Annotated[User, Depends(admin_user)],
):
    service.disable(item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
