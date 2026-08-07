from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .models import AdminUser, CollectionBatch, Product, ProductGroup, ProductGroupMembership, ProductSnapshot
from .schemas import IngestBatchRequest, ProductListItem, SnapshotResponse


SORT_COLUMNS = {
    "offer_id": Product.offer_id,
    "listing_date": Product.listing_date,
    "listed_days": Product.listed_days,
    "total_orders": Product.total_orders,
    "total_pieces": Product.total_pieces,
    "daily_orders": Product.daily_orders,
    "last_collected_at": Product.last_collected_at,
}


@dataclass
class ProductFilters:
    listing_date_from: date | None = None
    listing_date_to: date | None = None
    listed_days_min: int | None = None
    listed_days_max: int | None = None
    total_orders_min: int | None = None
    total_orders_max: int | None = None
    total_pieces_min: int | None = None
    total_pieces_max: int | None = None
    daily_orders_min: Decimal | None = None
    daily_orders_max: Decimal | None = None
    collected_at_from: datetime | None = None
    collected_at_to: datetime | None = None


def normalize_group_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def get_or_create_group(db: Session, owner_id: int, name: str) -> tuple[ProductGroup, bool]:
    cleaned_name = " ".join(name.split())
    normalized_name = normalize_group_name(cleaned_name)
    existing = db.scalar(
        select(ProductGroup).where(
            ProductGroup.owner_id == owner_id,
            ProductGroup.normalized_name == normalized_name,
        )
    )
    if existing is not None:
        return existing, False

    group = ProductGroup(owner_id=owner_id, name=cleaned_name, normalized_name=normalized_name)
    db.add(group)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(ProductGroup).where(
                ProductGroup.owner_id == owner_id,
                ProductGroup.normalized_name == normalized_name,
            )
        )
        if existing is None:
            raise
        return existing, False

    db.refresh(group)
    return group, True


def calculate_metrics(listed_days: int, total_orders: int, total_pieces: int) -> tuple[int, Decimal, Decimal | None]:
    calculation_days = 220 if listed_days > 360 else listed_days
    daily_orders = Decimal(0) if calculation_days == 0 else (
        Decimal(total_orders) / Decimal(calculation_days)
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    average = None if total_orders == 0 else (
        Decimal(total_pieces) / Decimal(total_orders)
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return calculation_days, daily_orders, average


def product_to_item(product: Product) -> ProductListItem:
    return ProductListItem(
        id=product.id,
        offer_id=product.offer_id,
        product_url=product.product_url,
        image_url=f"/images/{product.cached_image_name}" if product.cached_image_name else None,
        listing_date=product.listing_date,
        listed_days=product.listed_days,
        total_orders=product.total_orders,
        total_pieces=product.total_pieces,
        calculation_days=product.calculation_days,
        daily_orders=product.daily_orders,
        average_pieces_per_order=product.average_pieces_per_order,
        last_collected_at=product.last_collected_at,
    )


def snapshot_to_response(snapshot: ProductSnapshot) -> SnapshotResponse:
    return SnapshotResponse(
        id=snapshot.id,
        owner_id=snapshot.owner_id,
        group_id=snapshot.group_id,
        listing_date=snapshot.listing_date,
        listed_days=snapshot.listed_days,
        total_orders=snapshot.total_orders,
        total_pieces=snapshot.total_pieces,
        calculation_days=snapshot.calculation_days,
        daily_orders=snapshot.daily_orders,
        average_pieces_per_order=snapshot.average_pieces_per_order,
        collected_at=snapshot.collected_at,
    )


def ingest_batch(
    db: Session,
    owner: AdminUser,
    payload: IngestBatchRequest,
) -> tuple[CollectionBatch, bool, list[tuple[int, str]]]:
    existing = db.scalar(
        select(CollectionBatch).where(
            CollectionBatch.owner_id == owner.id,
            CollectionBatch.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        return existing, True, []

    group = db.scalar(
        select(ProductGroup).where(
            ProductGroup.id == payload.group_id,
            ProductGroup.owner_id == owner.id,
        )
    )
    if not group:
        raise LookupError("group not found")

    batch = CollectionBatch(
        owner_id=owner.id,
        idempotency_key=payload.idempotency_key,
        group_id=payload.group_id,
        source_page=payload.source_page,
        item_count=len(payload.products),
    )
    db.add(batch)
    db.flush()

    now = datetime.now(timezone.utc)
    images_to_cache: list[tuple[int, str]] = []
    for incoming in payload.products:
        product = db.scalar(select(Product).where(Product.offer_id == incoming.offer_id))
        calculation_days, daily_orders, average = calculate_metrics(
            incoming.listed_days, incoming.total_orders, incoming.total_pieces
        )
        image_url = str(incoming.image_url) if incoming.image_url else None
        if product is None:
            product = Product(
                offer_id=incoming.offer_id,
                product_url=str(incoming.product_url),
                original_image_url=image_url,
                image_status="pending" if image_url else "missing",
                listing_date=incoming.listing_date,
                listed_days=incoming.listed_days,
                total_orders=incoming.total_orders,
                total_pieces=incoming.total_pieces,
                calculation_days=calculation_days,
                daily_orders=daily_orders,
                average_pieces_per_order=average,
                last_collected_at=now,
            )
            db.add(product)
            db.flush()
        else:
            image_changed = bool(image_url and image_url != product.original_image_url)
            product.product_url = str(incoming.product_url)
            product.original_image_url = image_url
            product.listing_date = incoming.listing_date
            product.listed_days = incoming.listed_days
            product.total_orders = incoming.total_orders
            product.total_pieces = incoming.total_pieces
            product.calculation_days = calculation_days
            product.daily_orders = daily_orders
            product.average_pieces_per_order = average
            product.last_collected_at = now
            if image_changed or (image_url and not product.cached_image_name):
                product.image_status = "pending"

        membership = db.scalar(
            select(ProductGroupMembership).where(
                ProductGroupMembership.owner_id == owner.id,
                ProductGroupMembership.product_id == product.id,
                ProductGroupMembership.group_id == group.id,
            )
        )
        if membership is None:
            db.add(ProductGroupMembership(owner_id=owner.id, product_id=product.id, group_id=group.id))

        db.add(ProductSnapshot(
            owner_id=owner.id,
            batch_id=batch.id,
            product_id=product.id,
            group_id=group.id,
            listing_date=incoming.listing_date,
            listed_days=incoming.listed_days,
            total_orders=incoming.total_orders,
            total_pieces=incoming.total_pieces,
            calculation_days=calculation_days,
            daily_orders=daily_orders,
            average_pieces_per_order=average,
        ))
        if image_url and product.image_status == "pending":
            images_to_cache.append((product.id, image_url))

    db.commit()
    db.refresh(batch)
    return batch, False, images_to_cache


def get_product_with_relations(db: Session, product_id: int) -> Product | None:
    return db.scalar(
        select(Product)
        .options(
            selectinload(Product.memberships).selectinload(ProductGroupMembership.group),
            selectinload(Product.snapshots),
        )
        .where(Product.id == product_id)
    )


def apply_product_filters(statement, filters: ProductFilters):
    bounds = (
        (Product.listing_date, filters.listing_date_from, filters.listing_date_to),
        (Product.listed_days, filters.listed_days_min, filters.listed_days_max),
        (Product.total_orders, filters.total_orders_min, filters.total_orders_max),
        (Product.total_pieces, filters.total_pieces_min, filters.total_pieces_max),
        (Product.daily_orders, filters.daily_orders_min, filters.daily_orders_max),
        (Product.last_collected_at, filters.collected_at_from, filters.collected_at_to),
    )
    for column, minimum, maximum in bounds:
        if minimum is not None:
            statement = statement.where(column >= minimum)
        if maximum is not None:
            statement = statement.where(column <= maximum)
    return statement


def list_group_products(
    db: Session,
    group_id: int,
    owner_id: int,
    page: int,
    page_size: int,
    sort_field: str,
    sort_direction: str,
    filters: ProductFilters,
) -> tuple[list[Product], int]:
    column = SORT_COLUMNS[sort_field]
    membership_filter = Product.memberships.any(
        and_(
            ProductGroupMembership.group_id == group_id,
            ProductGroupMembership.owner_id == owner_id,
        )
    )
    filtered = apply_product_filters(select(Product).where(membership_filter), filters)
    count_statement = apply_product_filters(select(func.count(Product.id)).where(membership_filter), filters)
    total = db.scalar(count_statement) or 0
    order = column.asc() if sort_direction == "asc" else column.desc()
    products = list(db.scalars(
        filtered
        .order_by(order, Product.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ))
    return products, total
