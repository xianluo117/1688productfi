from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_current_user, get_ingest_user
from .config import get_settings
from .database import SessionLocal, get_db
from .image_cache import cache_product_image, normalize_temu_image_url
from .models import (
    AdminUser,
    CrossPlatformProductRelation,
    Product,
    TemuGroupMembership,
    TemuProduct,
    TemuProductGroup,
    TemuProductSnapshot,
    TemuSimilarityClusterMember,
)
from .schemas import (
    GroupCreate,
    IngestBatchResponse,
    SortDirection,
    TemuGroupResponse,
    TemuGroupUpdate,
    TemuIngestBatchRequest,
    TemuProductDetail,
    TemuProductPage,
    TemuSnapshotResponse,
    TemuSortField,
)
from .temu_services import (
    TemuFilters,
    get_or_create_temu_group,
    ingest_temu_batch,
    list_temu_group_products,
    temu_product_to_item,
)
from .services import product_to_item
from .similarity_services import enqueue_temu_product, queue_control

router = APIRouter(prefix="/api/temu", tags=["temu"])
settings = get_settings()
logger = logging.getLogger(__name__)


def resolve_owner_id(current_user: AdminUser, requested_owner_id: int | None) -> int:
    if current_user.role != "admin":
        if requested_owner_id is not None and requested_owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权访问其他用户的数据")
        return current_user.id
    return requested_owner_id or current_user.id


def get_group_or_404(db: Session, group_id: int, current_user: AdminUser) -> TemuProductGroup:
    group = db.get(TemuProductGroup, group_id)
    if not group or (current_user.role != "admin" and group.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Temu 分组不存在")
    return group


@router.get("/groups", response_model=list[TemuGroupResponse])
def list_groups(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    owner_id: int | None = None,
) -> list[TemuProductGroup]:
    resolved = resolve_owner_id(current_user, owner_id)
    return list(db.scalars(
        select(TemuProductGroup)
        .where(TemuProductGroup.owner_id == resolved)
        .order_by(TemuProductGroup.name.asc())
    ))


@router.post("/groups", response_model=TemuGroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    owner_id: int | None = None,
) -> TemuProductGroup:
    resolved = resolve_owner_id(current_user, owner_id)
    group, created = get_or_create_temu_group(db, resolved, payload.name)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return group


@router.put("/groups/{group_id}", response_model=TemuGroupResponse)
def update_group(
    group_id: int,
    payload: TemuGroupUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
) -> TemuProductGroup:
    group = get_group_or_404(db, group_id, current_user)
    if payload.name is not None:
        group.name = payload.name
        group.normalized_name = " ".join(payload.name.split()).casefold()
    if payload.default_sort_field is not None:
        group.default_sort_field = payload.default_sort_field
    if payload.default_sort_direction is not None:
        group.default_sort_direction = payload.default_sort_direction
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该用户下 Temu 分组名称已存在") from exc
    db.refresh(group)
    return group


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
) -> Response:
    group = get_group_or_404(db, group_id, current_user)
    db.delete(group)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/ingest/groups", response_model=list[TemuGroupResponse])
def ingest_groups(
    db: Annotated[Session, Depends(get_db)],
    ingest_user: Annotated[AdminUser, Depends(get_ingest_user)],
) -> list[TemuProductGroup]:
    return list(db.scalars(
        select(TemuProductGroup)
        .where(TemuProductGroup.owner_id == ingest_user.id)
        .order_by(TemuProductGroup.name.asc())
    ))


@router.post("/ingest/groups", response_model=TemuGroupResponse)
def ensure_ingest_group(
    payload: GroupCreate,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    ingest_user: Annotated[AdminUser, Depends(get_ingest_user)],
) -> TemuProductGroup:
    group, created = get_or_create_temu_group(db, ingest_user.id, payload.name)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return group


async def cache_temu_image_task(product_id: int, image_url: str) -> None:
    normalized_url = normalize_temu_image_url(image_url)
    try:
        with SessionLocal() as db:
            product = db.get(TemuProduct, product_id)
            if not product:
                return
            filename = await cache_product_image(
                product.temu_product_id,
                normalized_url,
                settings,
                namespace="temu",
            )
            image_changed = filename != product.cached_image_name
            product.image_url = normalized_url
            product.cached_image_name = filename
            product.image_status = "ready"
            if image_changed:
                product.image_content_hash = None
                product.image_phash = None
                product.similarity_status = "pending"
            if queue_control(db).auto_vectorization_enabled:
                enqueue_temu_product(db, product)
            else:
                product.similarity_status = "disabled"
            db.commit()
    except Exception as error:
        logger.exception(
            "Failed to cache Temu image for product %s from %s: %s",
            product_id,
            normalized_url,
            error,
        )
        with SessionLocal() as db:
            product = db.get(TemuProduct, product_id)
            if product:
                product.image_status = "failed"
                product.similarity_status = "failed"
                db.commit()


@router.post("/ingest/batches", response_model=IngestBatchResponse)
def create_ingest_batch(
    payload: TemuIngestBatchRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    ingest_user: Annotated[AdminUser, Depends(get_ingest_user)],
) -> IngestBatchResponse:
    try:
        batch, duplicate, images = ingest_temu_batch(db, ingest_user, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Temu 分组不存在或不属于当前令牌用户") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail="存在未可靠关联的 Temu 商品，已拒绝上传") from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Temu 上传冲突，请使用相同幂等键重试") from exc
    for product_id, image_url in images:
        background_tasks.add_task(cache_temu_image_task, product_id, image_url)
    return IngestBatchResponse(batch_id=batch.id, accepted=batch.item_count, duplicate=duplicate)


@router.get("/groups/{group_id}/products", response_model=TemuProductPage)
def group_products(
    group_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    sort_field: TemuSortField | None = None,
    sort_direction: SortDirection | None = None,
    search: str | None = None,
    daily_sales_min: int | None = Query(None, ge=0),
    daily_sales_max: int | None = Query(None, ge=0),
    weekly_sales_min: int | None = Query(None, ge=0),
    weekly_sales_max: int | None = Query(None, ge=0),
    total_sales_min: int | None = Query(None, ge=0),
    total_sales_max: int | None = Query(None, ge=0),
    sale_price_min: Decimal | None = Query(None, ge=0),
    sale_price_max: Decimal | None = Query(None, ge=0),
    original_price_min: Decimal | None = Query(None, ge=0),
    original_price_max: Decimal | None = Query(None, ge=0),
    listing_date_from: date | None = None,
    listing_date_to: date | None = None,
    listed_days_min: int | None = Query(None, ge=0),
    listed_days_max: int | None = Query(None, ge=0),
    shop_type: str | None = None,
    include_ads: bool = False,
    collected_at_from: datetime | None = None,
    collected_at_to: datetime | None = None,
) -> TemuProductPage:
    group = get_group_or_404(db, group_id, current_user)
    resolved_field = sort_field or group.default_sort_field
    resolved_direction = sort_direction or group.default_sort_direction
    filters = TemuFilters(
        search=search,
        daily_sales_min=daily_sales_min,
        daily_sales_max=daily_sales_max,
        weekly_sales_min=weekly_sales_min,
        weekly_sales_max=weekly_sales_max,
        total_sales_min=total_sales_min,
        total_sales_max=total_sales_max,
        sale_price_min=sale_price_min,
        sale_price_max=sale_price_max,
        original_price_min=original_price_min,
        original_price_max=original_price_max,
        listing_date_from=listing_date_from,
        listing_date_to=listing_date_to,
        listed_days_min=listed_days_min,
        listed_days_max=listed_days_max,
        shop_type=shop_type,
        include_ads=include_ads,
        collected_at_from=collected_at_from,
        collected_at_to=collected_at_to,
    )
    products, total = list_temu_group_products(
        db, group.id, group.owner_id, page, page_size, resolved_field, resolved_direction, filters
    )
    return TemuProductPage(
        items=[temu_product_to_item(product) for product in products],
        total=total,
        page=page,
        page_size=page_size,
        sort_field=resolved_field,
        sort_direction=resolved_direction,
    )


@router.get("/products/{product_id}", response_model=TemuProductDetail)
def product_detail(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    owner_id: int | None = None,
) -> TemuProductDetail:
    resolved_owner = resolve_owner_id(current_user, owner_id)
    product = db.get(TemuProduct, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Temu 商品不存在")
    memberships = list(db.scalars(select(TemuGroupMembership).where(
        TemuGroupMembership.product_id == product.id,
        TemuGroupMembership.owner_id == resolved_owner,
    )))
    if not memberships:
        raise HTTPException(status_code=404, detail="Temu 商品不存在")
    groups = list(db.scalars(select(TemuProductGroup).where(
        TemuProductGroup.id.in_([membership.group_id for membership in memberships])
    )))
    snapshots = list(db.scalars(
        select(TemuProductSnapshot)
        .where(
            TemuProductSnapshot.product_id == product.id,
            TemuProductSnapshot.owner_id == resolved_owner,
        )
        .order_by(TemuProductSnapshot.collected_at.desc())
        .limit(100)
    ))
    cluster_member = db.scalar(select(TemuSimilarityClusterMember).where(
        TemuSimilarityClusterMember.product_id == product.id
    ))
    same_products: list[TemuProduct] = []
    if cluster_member:
        same_products = list(db.scalars(select(TemuProduct).join(
            TemuSimilarityClusterMember, TemuSimilarityClusterMember.product_id == TemuProduct.id
        ).where(
            TemuSimilarityClusterMember.cluster_id == cluster_member.cluster_id,
            TemuProduct.id != product.id,
        ).order_by(TemuProduct.total_sales.desc())))
    source_products = list(db.scalars(select(Product).join(
        CrossPlatformProductRelation, CrossPlatformProductRelation.product_id == Product.id
    ).where(CrossPlatformProductRelation.temu_product_id == product.id).order_by(Product.total_pieces.desc())))
    item = temu_product_to_item(product)
    return TemuProductDetail(
        **item.model_dump(),
        groups=[TemuGroupResponse.model_validate(group) for group in groups],
        snapshots=[TemuSnapshotResponse.model_validate(snapshot) for snapshot in snapshots],
        platform_same_products=[temu_product_to_item(item) for item in same_products],
        source_products=[product_to_item(item) for item in source_products],
    )
