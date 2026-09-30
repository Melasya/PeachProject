import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionDep
from app.schemas import ItemCreate, ItemList, ItemRead, ItemUpdate
from app.services import items as items_service

router = APIRouter(prefix="/items", tags=["items"])


async def _get_or_404(session: AsyncSession, item_id: uuid.UUID):
    item = await items_service.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.get("", response_model=ItemList, summary="List items")
async def list_items(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ItemList:
    items, total = await items_service.list_items(session, limit=limit, offset=offset)
    return ItemList(items=[ItemRead.model_validate(item) for item in items], total=total)


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    payload: ItemCreate,
    session: SessionDep,
) -> ItemRead:
    item = await items_service.create_item(session, payload)
    return ItemRead.model_validate(item)


@router.get("/{item_id}", response_model=ItemRead)
async def get_item(
    item_id: uuid.UUID,
    session: SessionDep,
) -> ItemRead:
    return ItemRead.model_validate(await _get_or_404(session, item_id))


@router.patch("/{item_id}", response_model=ItemRead)
async def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    session: SessionDep,
) -> ItemRead:
    item = await _get_or_404(session, item_id)
    return ItemRead.model_validate(await items_service.update_item(session, item, payload))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    session: SessionDep,
) -> Response:
    item = await _get_or_404(session, item_id)
    await items_service.delete_item(session, item)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
