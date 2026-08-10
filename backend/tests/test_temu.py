from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AdminUser, TemuProduct, TemuProductSnapshot
from app.schemas import TemuIngestBatchRequest, TemuIngestProduct
from app.temu_services import (
    TemuFilters,
    derive_shop_type,
    get_or_create_temu_group,
    ingest_temu_batch,
    list_temu_group_products,
)


def create_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def create_user(db: Session, username: str) -> AdminUser:
    user = AdminUser(username=username, password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def incoming(
    product_id: str,
    daily_sales: int,
    *,
    is_ad: bool = False,
    image_url: str | None = "https://img.kwcdn.com/example.jpg",
) -> TemuIngestProduct:
    return TemuIngestProduct(
        temu_product_id=product_id,
        yunqi_gdid=f"9{product_id}",
        mall_id="100",
        title=f"Temu {product_id}",
        product_url=f"https://www.temu.com/example-g-{product_id}.html",
        image_url=image_url,
        sale_price=Decimal("12.86"),
        original_price=Decimal("20"),
        daily_sales=daily_sales,
        weekly_sales=212,
        monthly_sales=971,
        total_sales=17000,
        daily_revenue=Decimal("246.79"),
        weekly_revenue=Decimal("1800"),
        monthly_revenue=Decimal("8260"),
        total_revenue=Decimal("144670"),
        listing_date=date(2025, 12, 12),
        shop_type="半托管",
        temu_tags="Local|Top pick",
        is_ad=is_ad,
        association_status="商品ID匹配",
    )


def batch(group_id: int, key: str, products: list[TemuIngestProduct]) -> TemuIngestBatchRequest:
    return TemuIngestBatchRequest(
        idempotency_key=key,
        group_id=group_id,
        source_page=1,
        products=products,
    )


def test_temu_current_values_are_global_but_snapshots_are_owned():
    with create_session() as db:
        first = create_user(db, "first-temu")
        second = create_user(db, "second-temu")
        first_group, _ = get_or_create_temu_group(db, first.id, "A")
        second_group, _ = get_or_create_temu_group(db, second.id, "B")

        ingest_temu_batch(db, first, batch(first_group.id, "temu-first-key", [incoming("601", 10)]))
        ingest_temu_batch(db, second, batch(second_group.id, "temu-second-key", [incoming("601", 29)]))

        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "601"))
        assert product is not None
        assert product.daily_sales == 29
        snapshots = list(db.scalars(select(TemuProductSnapshot).where(TemuProductSnapshot.product_id == product.id)))
        assert {snapshot.owner_id for snapshot in snapshots} == {first.id, second.id}


def test_temu_batch_duplicate_product_id_uses_last_value():
    with create_session() as db:
        user = create_user(db, "dedupe-temu")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        created, duplicate = ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-dedupe-key", [incoming("602", 1), incoming("602", 8)]),
        )
        assert duplicate is False
        assert created.item_count == 1
        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "602"))
        assert product.daily_sales == 8


def test_temu_empty_image_does_not_replace_existing_product_image():
    with create_session() as db:
        user = create_user(db, "image-preserve-temu")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        original_url = "https://img.kwcdn.com/original.jpg"

        ingest_temu_batch(
            db,
            user,
            batch(
                group.id,
                "temu-image-original",
                [incoming("605", 5, image_url=original_url)],
            ),
        )
        ingest_temu_batch(
            db,
            user,
            batch(
                group.id,
                "temu-image-empty",
                [incoming("605", 6, image_url=None)],
            ),
        )

        product = db.scalar(
            select(TemuProduct).where(TemuProduct.temu_product_id == "605")
        )
        assert product is not None
        assert product.image_url == original_url
        assert product.daily_sales == 6


def test_temu_valid_image_replaces_existing_product_image():
    with create_session() as db:
        user = create_user(db, "image-update-temu")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        updated_url = "https://img.kwcdn.com/updated.jpg"

        ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-image-first", [incoming("606", 5)]),
        )
        ingest_temu_batch(
            db,
            user,
            batch(
                group.id,
                "temu-image-update",
                [incoming("606", 6, image_url=updated_url)],
            ),
        )

        product = db.scalar(
            select(TemuProduct).where(TemuProduct.temu_product_id == "606")
        )
        assert product is not None
        assert product.image_url == updated_url


def test_temu_list_hides_ads_by_default_and_derives_shop_type():
    with create_session() as db:
        user = create_user(db, "filter-temu")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-filter-key", [incoming("603", 5), incoming("604", 6, is_ad=True)]),
        )

        products, total = list_temu_group_products(
            db, group.id, user.id, 1, 30, "daily_sales", "desc", TemuFilters()
        )
        assert total == 1
        assert [product.temu_product_id for product in products] == ["603"]
        assert derive_shop_type(products[0].shop_type, products[0].temu_tags) == "半托Y1"

        products, total = list_temu_group_products(
            db, group.id, user.id, 1, 30, "daily_sales", "desc", TemuFilters(include_ads=True)
        )
        assert total == 2
