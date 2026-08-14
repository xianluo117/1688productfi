from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .image_cache import normalize_temu_image_url
from .temu_image_download_services import enqueue_temu_image_download
from .models import (
    AdminUser,
    TemuCollectionBatch,
    TemuGroupMembership,
    TemuProduct,
    TemuProductFavorite,
    TemuProductGroup,
    TemuProductSnapshot,
    TemuSimilarityClusterMember,
)
from .schemas import TemuIngestBatchRequest, TemuProductListItem
from .services import normalize_group_name


TEMU_SORT_COLUMNS = {
    "temu_product_id": TemuProduct.temu_product_id,
    "sale_price": TemuProduct.sale_price,
    "original_price": TemuProduct.original_price,
    "daily_sales": TemuProduct.daily_sales,
    "weekly_sales": TemuProduct.weekly_sales,
    "total_sales": TemuProduct.total_sales,
    "listing_date": TemuProduct.listing_date,
    "last_collected_at": TemuProduct.last_collected_at,
}


@dataclass
class TemuFilters:
    search: str | None = None
    daily_sales_min: int | None = None
    daily_sales_max: int | None = None
    weekly_sales_min: int | None = None
    weekly_sales_max: int | None = None
    total_sales_min: int | None = None
    total_sales_max: int | None = None
    sale_price_min: Decimal | None = None
    sale_price_max: Decimal | None = None
    original_price_min: Decimal | None = None
    original_price_max: Decimal | None = None
    listing_date_from: date | None = None
    listing_date_to: date | None = None
    listed_days_min: int | None = None
    listed_days_max: int | None = None
    shop_type: str | None = None
    include_ads: bool = False
    collected_at_from: datetime | None = None
    collected_at_to: datetime | None = None


def derive_shop_type(shop_type: str | None, temu_tags: str | None) -> str | None:
    if not shop_type:
        return None
    if "半托管" not in shop_type:
        return shop_type
    tags = {tag.strip().casefold() for tag in (temu_tags or "").split("|") if tag.strip()}
    return "半托Y1" if "local" in tags else "半托Y2"


def listed_days(listing_date: date | None, today: date | None = None) -> int | None:
    if listing_date is None:
        return None
    return max(0, ((today or date.today()) - listing_date).days)


def temu_product_to_item(product: TemuProduct) -> TemuProductListItem:
    return TemuProductListItem(
        id=product.id,
        temu_product_id=product.temu_product_id,
        yunqi_gdid=product.yunqi_gdid,
        mall_id=product.mall_id,
        title=product.title,
        product_url=product.product_url,
        image_url=product.image_url,
        sale_price=product.sale_price,
        original_price=product.original_price,
        daily_sales=product.daily_sales,
        weekly_sales=product.weekly_sales,
        monthly_sales=product.monthly_sales,
        total_sales=product.total_sales,
        daily_revenue=product.daily_revenue,
        weekly_revenue=product.weekly_revenue,
        monthly_revenue=product.monthly_revenue,
        total_revenue=product.total_revenue,
        listing_date=product.listing_date,
        listed_days=listed_days(product.listing_date),
        category=product.category,
        rating=product.rating,
        review_count=product.review_count,
        shop_name=product.shop_name,
        shop_type=product.shop_type,
        derived_shop_type=derive_shop_type(product.shop_type, product.temu_tags),
        shop_opened_at=product.shop_opened_at,
        shop_total_sales=product.shop_total_sales,
        temu_tags=product.temu_tags,
        is_ad=product.is_ad,
        delivery_tip=product.delivery_tip,
        last_collected_at=product.last_collected_at,
        cluster_id=getattr(product, "cluster_id", None),
        cluster_member_count=getattr(product, "cluster_member_count", 1),
        groups=getattr(product, "scope_groups", []),
        is_favorite=getattr(product, "is_favorite", False),
    )


def get_or_create_temu_group(db: Session, owner_id: int, name: str) -> tuple[TemuProductGroup, bool]:
    cleaned_name = " ".join(name.split())
    normalized_name = normalize_group_name(cleaned_name)
    existing = db.scalar(select(TemuProductGroup).where(
        TemuProductGroup.owner_id == owner_id,
        TemuProductGroup.normalized_name == normalized_name,
    ))
    if existing:
        return existing, False
    group = TemuProductGroup(owner_id=owner_id, name=cleaned_name, normalized_name=normalized_name)
    db.add(group)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(TemuProductGroup).where(
            TemuProductGroup.owner_id == owner_id,
            TemuProductGroup.normalized_name == normalized_name,
        ))
        if existing is None:
            raise
        return existing, False
    db.refresh(group)
    return group, True


def ingest_temu_batch(
    db: Session,
    owner: AdminUser,
    payload: TemuIngestBatchRequest,
) -> tuple[TemuCollectionBatch, bool, list[tuple[int, str]]]:
    existing = db.scalar(select(TemuCollectionBatch).where(
        TemuCollectionBatch.owner_id == owner.id,
        TemuCollectionBatch.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        return existing, True, []

    group = db.scalar(select(TemuProductGroup).where(
        TemuProductGroup.id == payload.group_id,
        TemuProductGroup.owner_id == owner.id,
    ))
    if not group:
        raise LookupError("group not found")

    # The final occurrence wins when a client sends duplicate product IDs.
    deduplicated = {incoming.temu_product_id: incoming for incoming in payload.products}
    batch = TemuCollectionBatch(
        owner_id=owner.id,
        idempotency_key=payload.idempotency_key,
        group_id=group.id,
        source_page=payload.source_page,
        item_count=len(deduplicated),
    )
    db.add(batch)
    db.flush()
    now = datetime.now(timezone.utc)
    images_to_cache: list[tuple[int, str]] = []

    for incoming in deduplicated.values():
        if incoming.association_status not in {"商品ID匹配", "容器关联"}:
            raise ValueError("unreliable Temu association")
        product = db.scalar(select(TemuProduct).where(
            TemuProduct.temu_product_id == incoming.temu_product_id
        ))
        normalized_image_url = (
            normalize_temu_image_url(str(incoming.image_url))
            if incoming.image_url
            else None
        )
        values = {
            "yunqi_gdid": incoming.yunqi_gdid,
            "mall_id": incoming.mall_id,
            "title": incoming.title,
            "product_url": str(incoming.product_url),
            "image_url": normalized_image_url,
            "sale_price": incoming.sale_price,
            "original_price": incoming.original_price,
            "daily_sales": incoming.daily_sales,
            "weekly_sales": incoming.weekly_sales,
            "monthly_sales": incoming.monthly_sales,
            "total_sales": incoming.total_sales,
            "daily_revenue": incoming.daily_revenue,
            "weekly_revenue": incoming.weekly_revenue,
            "monthly_revenue": incoming.monthly_revenue,
            "total_revenue": incoming.total_revenue,
            "listing_date": incoming.listing_date,
            "category": incoming.category,
            "rating": incoming.rating,
            "review_count": incoming.review_count,
            "shop_name": incoming.shop_name,
            "shop_type": incoming.shop_type,
            "shop_opened_at": incoming.shop_opened_at,
            "shop_total_sales": incoming.shop_total_sales,
            "temu_tags": incoming.temu_tags,
            "is_ad": incoming.is_ad,
            "delivery_tip": incoming.delivery_tip,
            "last_collected_at": now,
        }
        previous_image_url = product.image_url if product else None
        if product is None:
            product = TemuProduct(
                temu_product_id=incoming.temu_product_id,
                image_status="pending" if incoming.image_url else "missing",
                **values,
            )
            db.add(product)
            db.flush()
        else:
            for key, value in values.items():
                if key == "image_url" and value is None and product.image_url:
                    continue
                setattr(product, key, value)
        current_image_url = normalized_image_url or previous_image_url
        if current_image_url and (current_image_url != previous_image_url or not product.cached_image_name):
            product.image_status = "pending"
            product.image_content_hash = None
            product.image_phash = None
            product.similarity_status = "pending"
            enqueue_temu_image_download(
                db,
                product,
                owner_id=owner.id,
                group_id=group.id,
                source="ingest",
                force=True,
            )
            images_to_cache.append((product.id, current_image_url))

        membership = db.scalar(select(TemuGroupMembership).where(
            TemuGroupMembership.owner_id == owner.id,
            TemuGroupMembership.product_id == product.id,
            TemuGroupMembership.group_id == group.id,
        ))
        if membership is None:
            db.add(TemuGroupMembership(owner_id=owner.id, product_id=product.id, group_id=group.id))

        db.add(TemuProductSnapshot(
            owner_id=owner.id,
            batch_id=batch.id,
            product_id=product.id,
            group_id=group.id,
            sale_price=incoming.sale_price,
            original_price=incoming.original_price,
            daily_sales=incoming.daily_sales,
            weekly_sales=incoming.weekly_sales,
            monthly_sales=incoming.monthly_sales,
            total_sales=incoming.total_sales,
            daily_revenue=incoming.daily_revenue,
            weekly_revenue=incoming.weekly_revenue,
            monthly_revenue=incoming.monthly_revenue,
            total_revenue=incoming.total_revenue,
            listing_date=incoming.listing_date,
            shop_type=incoming.shop_type,
            temu_tags=incoming.temu_tags,
            is_ad=incoming.is_ad,
        ))

    db.commit()
    db.refresh(batch)
    return batch, False, images_to_cache


def apply_temu_filters(statement, filters: TemuFilters):
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        statement = statement.where(or_(
            TemuProduct.temu_product_id.ilike(pattern),
            TemuProduct.title.ilike(pattern),
        ))
    bounds = (
        (TemuProduct.daily_sales, filters.daily_sales_min, filters.daily_sales_max),
        (TemuProduct.weekly_sales, filters.weekly_sales_min, filters.weekly_sales_max),
        (TemuProduct.total_sales, filters.total_sales_min, filters.total_sales_max),
        (TemuProduct.sale_price, filters.sale_price_min, filters.sale_price_max),
        (TemuProduct.original_price, filters.original_price_min, filters.original_price_max),
        (TemuProduct.listing_date, filters.listing_date_from, filters.listing_date_to),
        (TemuProduct.last_collected_at, filters.collected_at_from, filters.collected_at_to),
    )
    for column, minimum, maximum in bounds:
        if minimum is not None:
            statement = statement.where(column >= minimum)
        if maximum is not None:
            statement = statement.where(column <= maximum)
    if filters.listed_days_min is not None:
        statement = statement.where(TemuProduct.listing_date <= date.today())
        statement = statement.where(TemuProduct.listing_date <= date.fromordinal(date.today().toordinal() - filters.listed_days_min))
    if filters.listed_days_max is not None:
        statement = statement.where(TemuProduct.listing_date >= date.fromordinal(date.today().toordinal() - filters.listed_days_max))
    if not filters.include_ads:
        statement = statement.where(TemuProduct.is_ad.is_(False))
    if filters.shop_type:
        selected = filters.shop_type
        if selected == "半托Y1":
            statement = statement.where(TemuProduct.shop_type.contains("半托管"), TemuProduct.temu_tags.ilike("%Local%"))
        elif selected == "半托Y2":
            statement = statement.where(TemuProduct.shop_type.contains("半托管"), or_(TemuProduct.temu_tags.is_(None), ~TemuProduct.temu_tags.ilike("%Local%")))
        else:
            statement = statement.where(TemuProduct.shop_type == selected)
    return statement


def attach_temu_scope_metadata(
    db: Session,
    products: list[TemuProduct],
    owner_id: int,
) -> None:
    product_ids = [product.id for product in products]
    if not product_ids:
        return
    groups_by_product: dict[int, list[dict[str, object]]] = {}
    for product_id, group_id, group_name in db.execute(
        select(
            TemuGroupMembership.product_id,
            TemuProductGroup.id,
            TemuProductGroup.name,
        )
        .join(TemuProductGroup, TemuProductGroup.id == TemuGroupMembership.group_id)
        .where(
            TemuGroupMembership.owner_id == owner_id,
            TemuGroupMembership.product_id.in_(product_ids),
        )
        .order_by(TemuProductGroup.name.asc())
    ):
        groups_by_product.setdefault(product_id, []).append(
            {"id": group_id, "name": group_name}
        )
    favorite_ids = set(
        db.scalars(
            select(TemuProductFavorite.product_id).where(
                TemuProductFavorite.owner_id == owner_id,
                TemuProductFavorite.product_id.in_(product_ids),
            )
        )
    )
    for product in products:
        product.scope_groups = groups_by_product.get(product.id, [])
        product.is_favorite = product.id in favorite_ids


def list_temu_products(
    db: Session,
    owner_id: int,
    page: int,
    page_size: int,
    sort_field: str,
    sort_direction: str,
    filters: TemuFilters,
    *,
    group_id: int | None = None,
    favorites_only: bool = False,
) -> tuple[list[TemuProduct], int]:
    scope_filters = [
        TemuProduct.memberships.any(TemuGroupMembership.owner_id == owner_id)
    ]
    if group_id is not None:
        scope_filters.append(
            TemuProduct.memberships.any(
                and_(
                    TemuGroupMembership.group_id == group_id,
                    TemuGroupMembership.owner_id == owner_id,
                )
            )
        )
    if favorites_only:
        scope_filters.append(
            TemuProduct.favorites.any(TemuProductFavorite.owner_id == owner_id)
        )
    cluster_counts = (
        select(
            TemuSimilarityClusterMember.product_id.label("cluster_product_id"),
            TemuSimilarityClusterMember.cluster_id.label("cluster_id"),
            func.count(TemuSimilarityClusterMember.id)
            .over(partition_by=TemuSimilarityClusterMember.cluster_id)
            .label("cluster_member_count"),
        ).subquery()
    )
    filtered = apply_temu_filters(
        select(
            TemuProduct,
            cluster_counts.c.cluster_id,
            cluster_counts.c.cluster_member_count,
        )
        .outerjoin(cluster_counts, cluster_counts.c.cluster_product_id == TemuProduct.id)
        .where(*scope_filters),
        filters,
    )
    counted = apply_temu_filters(
        select(func.count(TemuProduct.id)).where(*scope_filters), filters
    )
    total = int(db.scalar(counted) or 0)
    column = TEMU_SORT_COLUMNS[sort_field]
    order = column.asc() if sort_direction == "asc" else column.desc()
    rows = db.execute(
        filtered.order_by(order, TemuProduct.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    products: list[TemuProduct] = []
    for product, cluster_id, member_count in rows:
        product.cluster_id = cluster_id
        product.cluster_member_count = int(member_count or 1)
        products.append(product)
    attach_temu_scope_metadata(db, products, owner_id)
    return products, total


def list_temu_group_products(
    db: Session,
    group_id: int,
    owner_id: int,
    page: int,
    page_size: int,
    sort_field: str,
    sort_direction: str,
    filters: TemuFilters,
) -> tuple[list[TemuProduct], int]:
    return list_temu_products(
        db,
        owner_id,
        page,
        page_size,
        sort_field,
        sort_direction,
        filters,
        group_id=group_id,
    )
