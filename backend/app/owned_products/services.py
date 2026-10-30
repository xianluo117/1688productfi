from collections import defaultdict
from typing import TypeVar

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import OwnedBatch, OwnedColor, OwnedItem


Record = TypeVar("Record", OwnedBatch, OwnedItem, OwnedColor)


async def get_record(db: AsyncSession, model: type[Record], record_id: int) -> Record:
    record = await db.get(model, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return record


async def create_record(
    db: AsyncSession,
    model: type[Record],
    payload: BaseModel,
    parent_model: type[OwnedBatch] | type[OwnedItem] | None = None,
    **parent_fields: int,
) -> Record:
    async with db.begin():
        if parent_model is not None:
            await get_record(db, parent_model, next(iter(parent_fields.values())))
        record = model(**payload.model_dump(exclude_unset=True), **parent_fields)
        db.add(record)
        await db.flush()
    return record


async def patch_record(db: AsyncSession, model: type[Record], record_id: int, payload: BaseModel) -> Record:
    values = payload.model_dump(exclude_unset=True)
    async with db.begin():
        if not values:
            return await get_record(db, model, record_id)
        record = await db.scalar(
            update(model)
            .where(model.id == record_id)
            .values(**values, updated_at=func.now())
            .returning(model)
        )
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
    return record


async def list_records(
    db: AsyncSession,
    model: type[Record],
    page: int,
    page_size: int,
    **parent_fields: int,
) -> dict:
    conditions = [getattr(model, name) == value for name, value in parent_fields.items()]
    total = await db.scalar(select(func.count()).select_from(model).where(*conditions))
    records = await db.scalars(
        select(model).where(*conditions).order_by(model.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {"items": list(records), "total": total, "page": page, "page_size": page_size}


async def items_by_new_code(db: AsyncSession, new_item_code: str) -> dict:
    rows = (await db.execute(
        select(OwnedItem, OwnedBatch)
        .join(OwnedBatch, OwnedItem.batch_id == OwnedBatch.id)
        .where(OwnedItem.new_item_code == new_item_code)
        .order_by(OwnedItem.id.desc())
    )).all()
    colors_by_item = defaultdict(list)
    if rows:
        colors = await db.scalars(
            select(OwnedColor)
            .where(OwnedColor.item_id.in_([item.id for item, _ in rows]))
            .order_by(OwnedColor.id.asc())
        )
        for color in colors:
            colors_by_item[color.item_id].append(color)
    return {
        "items": [
            {"item": item, "batch": batch, "colors": colors_by_item[item.id]}
            for item, batch in rows
        ],
        "total": len(rows),
    }
