import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item
from app.schemas import ItemCreate, ItemUpdate


async def list_items(session: AsyncSession, *, limit: int, offset: int) -> tuple[list[Item], int]:
    total = await session.scalar(select(func.count()).select_from(Item)) or 0
    result = await session.execute(
        select(Item).order_by(Item.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars()), total


async def get_item(session: AsyncSession, item_id: uuid.UUID) -> Item | None:
    return await session.get(Item, item_id)


async def create_item(session: AsyncSession, payload: ItemCreate) -> Item:
    item = Item(**payload.model_dump())
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return item


async def update_item(session: AsyncSession, item: Item, payload: ItemUpdate) -> Item:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await session.flush()
    await session.refresh(item)
    return item


async def delete_item(session: AsyncSession, item: Item) -> None:
    await session.delete(item)
    await session.flush()
