from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import CollectionBatch, Product, ProductGroup, ProductGroupMembership, ProductSnapshot
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


def normalize_group_name(name: str) -> str:
    return " ".join(name.split()).casefold()


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


def ingest_batch(db: Session, payload: IngestBatchRequest) -> tuple[CollectionBatch, bool, list[tuple[int, str]]]:
    existing = db.scalar(select(CollectionBatch).where(CollectionBatch.idempotency_key == payload.idempotency_key))
    if existing:
        return existing, True, []

    group = db.get(ProductGroup, payload.group_id)
    if not group:
        raise LookupError("group not found")

    batch = CollectionBatch(
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
                ProductGroupMembership.product_id == product.id,
                ProductGroupMembership.group_id == group.id,
            )
        )
        if membership is None:
            db.add(ProductGroupMembership(product_id=product.id, group_id=group.id))

        db.add(ProductSnapshot(
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


def list_group_products(
    db: Session,
    group_id: int,
    page: int,
    page_size: int,
    sort_field: str,
    sort_direction: str,
) -> tuple[list[Product], int]:
    column = SORT_COLUMNS[sort_field]
    membership_filter = Product.memberships.any(ProductGroupMembership.group_id == group_id)
    total = db.scalar(select(func.count(Product.id)).where(membership_filter)) or 0
    order = column.asc() if sort_direction == "asc" else column.desc()
    products = list(db.scalars(
        select(Product)
        .where(membership_filter)
        .order_by(order, Product.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ))
    return products, total
