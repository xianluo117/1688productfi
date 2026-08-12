from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.image_cache import normalize_temu_image_url
from app.models import (
    AdminUser,
    TemuImageDownloadTask,
    TemuProduct,
    TemuProductSnapshot,
)
from app.schemas import TemuIngestBatchRequest, TemuIngestProduct
from app.temu_image_download_services import enqueue_temu_image_scope
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


def test_normalize_temu_kwcdn_image_url_removes_transform_query():
    assert normalize_temu_image_url(
        "https://img.kwcdn.com/product/fancy/example.jpg?imageView2/2/w/500/q/70/format/avif"
    ) == "https://img.kwcdn.com/product/fancy/example.jpg"


def test_normalize_non_kwcdn_image_url_preserves_query():
    assert normalize_temu_image_url(
        "https://cdn.example.com/example.jpg?token=abc#preview"
    ) == "https://cdn.example.com/example.jpg?token=abc"


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


def test_temu_ingest_stores_original_kwcdn_image_url():
    with create_session() as db:
        user = create_user(db, "original-image-temu")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        transformed_url = (
            "https://img.kwcdn.com/product/fancy/example.jpg"
            "?imageView2/2/w/500/q/70/format/avif"
        )

        _, _, images = ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-original-image", [incoming("600", 1, image_url=transformed_url)]),
        )

        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "600"))
        assert product is not None
        assert product.image_url == "https://img.kwcdn.com/product/fancy/example.jpg"
        assert images == [(product.id, product.image_url)]
        task = db.scalar(
            select(TemuImageDownloadTask).where(
                TemuImageDownloadTask.product_id == product.id
            )
        )
        assert task is not None
        assert task.status == "pending"
        assert task.source == "ingest"
        assert task.image_url == product.image_url


def test_temu_batch_duplicate_product_id_uses_last_value():
    with create_session() as db:
        user = create_user(db, "dedupe-temu")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        created, duplicate, images = ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-dedupe-key", [incoming("602", 1), incoming("602", 8)]),
        )
        assert duplicate is False
        assert len(images) == 1
        assert created.item_count == 1
        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "602"))
        assert product.daily_sales == 8


def test_manual_image_scope_skips_ready_file_and_enqueues_failed_file(tmp_path: Path):
    with create_session() as db:
        user = create_user(db, "manual-image-temu")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(
                group.id,
                "temu-manual-images",
                [incoming("610", 1), incoming("611", 2)],
            ),
        )
        ready = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "610"))
        failed = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "611"))
        assert ready is not None and failed is not None
        ready.cached_image_name = "ready.jpg"
        ready.image_status = "ready"
        (tmp_path / "ready.jpg").write_bytes(b"ready")
        failed.image_status = "failed"
        db.query(TemuImageDownloadTask).delete()
        db.flush()

        assert enqueue_temu_image_scope(db, user.id, [group.id], tmp_path) == 1
        task = db.scalar(select(TemuImageDownloadTask))
        assert task is not None
        assert task.product_id == failed.id
        assert task.source == "manual"


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
