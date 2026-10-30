from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from . import services
from .database import get_db
from .models import OwnedBatch, OwnedColor, OwnedItem
from .schemas import (
    AggregateResponse,
    BatchCreate,
    BatchPatch,
    BatchResponse,
    ColorCreate,
    ColorPatch,
    ColorResponse,
    ItemCreate,
    ItemPatch,
    ItemResponse,
    Page,
)


router = APIRouter(prefix="/api/owned-products", tags=["自有产品"])
Database = Annotated[AsyncSession, Depends(get_db)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1)]


@router.post("/batches", response_model=BatchResponse, status_code=201, summary="新增批次")
async def create_batch(payload: BatchCreate, db: Database):
    return await services.create_record(db, OwnedBatch, payload)


@router.get("/batches", response_model=Page[BatchResponse], summary="批次列表")
async def list_batches(db: Database, page: PageNumber = 1, page_size: PageSize = 30):
    return await services.list_records(db, OwnedBatch, page, page_size)


@router.patch("/batches/{batch_id}", response_model=BatchResponse, summary="部分修改批次")
async def patch_batch(batch_id: int, payload: BatchPatch, db: Database):
    return await services.patch_record(db, OwnedBatch, batch_id, payload)


@router.get("/batches/{batch_id}", response_model=BatchResponse, summary="批次详情")
async def get_batch(batch_id: int, db: Database):
    return await services.get_record(db, OwnedBatch, batch_id)


@router.post("/batches/{batch_id}/items", response_model=ItemResponse, status_code=201, summary="新增货号")
async def create_item(batch_id: int, payload: ItemCreate, db: Database):
    return await services.create_record(db, OwnedItem, payload, OwnedBatch, batch_id=batch_id)


@router.get("/batches/{batch_id}/items", response_model=Page[ItemResponse], summary="货号列表")
async def list_items(batch_id: int, db: Database, page: PageNumber = 1, page_size: PageSize = 30):
    await services.get_record(db, OwnedBatch, batch_id)
    return await services.list_records(db, OwnedItem, page, page_size, batch_id=batch_id)


@router.get(
    "/items/by-new-code",
    response_model=AggregateResponse,
    summary="按新货号跨批次精确聚合查询",
    description="返回全部匹配货号、各自完整批次和全部颜色；不分页、不去重。货号 ID 倒序，颜色 ID 升序。",
)
async def items_by_new_code(db: Database, new_item_code: Annotated[str, Query(description="新货号原文，精确匹配")]):
    return await services.items_by_new_code(db, new_item_code)


@router.patch("/items/{item_id}", response_model=ItemResponse, summary="部分修改货号")
async def patch_item(item_id: int, payload: ItemPatch, db: Database):
    return await services.patch_record(db, OwnedItem, item_id, payload)


@router.get("/items/{item_id}", response_model=ItemResponse, summary="货号详情")
async def get_item(item_id: int, db: Database):
    return await services.get_record(db, OwnedItem, item_id)


@router.post("/items/{item_id}/colors", response_model=ColorResponse, status_code=201, summary="新增颜色")
async def create_color(item_id: int, payload: ColorCreate, db: Database):
    return await services.create_record(db, OwnedColor, payload, OwnedItem, item_id=item_id)


@router.get("/items/{item_id}/colors", response_model=Page[ColorResponse], summary="颜色列表")
async def list_colors(item_id: int, db: Database, page: PageNumber = 1, page_size: PageSize = 30):
    await services.get_record(db, OwnedItem, item_id)
    return await services.list_records(db, OwnedColor, page, page_size, item_id=item_id)


@router.patch("/colors/{color_id}", response_model=ColorResponse, summary="部分修改颜色")
async def patch_color(color_id: int, payload: ColorPatch, db: Database):
    return await services.patch_record(db, OwnedColor, color_id, payload)


@router.get("/colors/{color_id}", response_model=ColorResponse, summary="颜色详情")
async def get_color(color_id: int, db: Database):
    return await services.get_record(db, OwnedColor, color_id)
