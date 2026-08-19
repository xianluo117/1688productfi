import asyncio
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import httpx
from openpyxl import load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.image_cache import normalize_temu_image_url
from app.models import (
    AdminUser,
    TemuImageDownloadTask,
    TemuImageSearchResult,
    TemuImageSearchResultItem,
    TemuImageSearchTask,
    TemuGroupMembership,
    TemuProduct,
    TemuProductFavorite,
    TemuProductGroup,
    TemuProductSnapshot,
)
from app.schemas import TemuIngestBatchRequest, TemuIngestProduct
from app.temu_image_download_services import enqueue_temu_image_scope
from app.temu_image_search_client import cancel_remote_tasks
from app.temu_favorite_services import build_favorites_workbook, set_temu_favorite
from app.temu_image_search_services import (
    claim_temu_image_search,
    config_payload,
    enqueue_temu_image_search,
    enqueue_temu_image_search_scope,
    image_search_config,
    image_search_stats,
    image_search_summaries,
    normalize_result_image_url,
    replace_image_search_result,
    resolve_search_image_url,
    set_image_search_queue_state,
    update_image_search_config,
)
from app.temu_services import (
    TemuFilters,
    derive_shop_type,
    get_or_create_temu_group,
    ingest_temu_batch,
    list_temu_group_products,
    remove_temu_group,
    remove_temu_product_memberships,
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


def test_image_search_config_encrypts_and_masks_api_key():
    with create_session() as db:
        admin = create_user(db, "image-search-config")
        settings = Settings(
            _env_file=None,
            admin_password="test-admin-password",
            session_secret="test-session-secret",
            database_url="sqlite+pysqlite:///:memory:",
        )
        config = image_search_config(db)
        update_image_search_config(
            db,
            config,
            base_url="https://search.example.com/",
            api_key="search-secret-key",
            public_image_base_url="https://products.example.com/",
            auto_search_enabled=True,
            concurrency=7,
            timeout_seconds=30,
            poll_interval_seconds=2,
            task_timeout_seconds=180,
            updated_by=admin.id,
            settings=settings,
        )

        assert config.base_url == "https://search.example.com"
        assert config.public_image_base_url == "https://products.example.com"
        assert config.api_key_encrypted != "search-secret-key"
        payload = config_payload(config, settings)
        assert payload["api_key_configured"] is True
        assert payload["api_key_masked"] == "sea***key"
        assert payload["auto_search_enabled"] is True
        assert payload["queue_state"] == "running"
        assert payload["public_image_base_url"] == "https://products.example.com"
        assert payload["concurrency"] == 7
        assert normalize_result_image_url("//cbu01.alicdn.com/a.jpg") == "https://cbu01.alicdn.com/a.jpg"
        assert normalize_result_image_url("http://cbu01.alicdn.com/a.jpg") == "https://cbu01.alicdn.com/a.jpg"
        assert normalize_result_image_url("not-a-url") is None


def test_image_search_enqueue_requires_ready_image_and_deduplicates():
    with create_session() as db:
        user = create_user(db, "image-search-dedupe")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-image-search-dedupe", [incoming("620", 1)]),
        )
        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "620"))
        assert product is not None
        assert enqueue_temu_image_search(
            db,
            product,
            owner_id=user.id,
            group_id=group.id,
            source="image_ready",
        ) is None

        product.image_status = "ready"
        first = enqueue_temu_image_search(
            db,
            product,
            owner_id=user.id,
            group_id=group.id,
            source="image_ready",
        )
        db.flush()
        second = enqueue_temu_image_search(
            db,
            product,
            owner_id=user.id,
            group_id=group.id,
            source="image_ready",
        )
        assert first is not None
        assert second is first
        assert db.query(TemuImageSearchTask).count() == 1


def test_manual_image_search_scope_only_enqueues_ready_products():
    with create_session() as db:
        user = create_user(db, "image-search-scope")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(
                group.id,
                "temu-image-search-scope",
                [incoming("621", 1), incoming("622", 2)],
            ),
        )
        ready = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "621"))
        pending = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "622"))
        assert ready is not None and pending is not None
        ready.image_status = "ready"
        pending.image_status = "pending"

        assert enqueue_temu_image_search_scope(db, user.id, [group.id]) == 1
        task = db.scalar(select(TemuImageSearchTask))
        assert task is not None
        assert task.product_id == ready.id
        assert task.source == "manual"
        stats = image_search_stats(db, user.id, [group.id])
        assert stats["total"] == 2
        assert stats["searchable"] == 1
        assert stats["pending"] == 1


def test_image_search_result_saves_top_three_and_builds_summary():
    with create_session() as db:
        user = create_user(db, "image-search-result")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-image-search-result", [incoming("623", 1)]),
        )
        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "623"))
        assert product is not None
        product.image_status = "ready"
        task = enqueue_temu_image_search(
            db,
            product,
            owner_id=user.id,
            group_id=group.id,
            source="manual",
        )
        assert task is not None
        db.flush()
        replace_image_search_result(
            db,
            task,
            {
                "search_page_url": "https://air.1688.com/search",
                "image_id": "image-623",
                "found": 698,
                "products": [
                    {
                        "image": f"https://cbu01.alicdn.com/{rank}.jpg",
                        "title": f"Product {rank}",
                        "price": rank,
                        "sale_quantity": rank * 10,
                        "product_url": f"https://detail.1688.com/offer/{rank}.html",
                    }
                    for rank in range(1, 5)
                ],
            },
        )
        task.status = "completed"
        db.flush()

        result = db.scalar(select(TemuImageSearchResult))
        assert result is not None
        assert db.query(TemuImageSearchResultItem).count() == 3
        summary = image_search_summaries(db, [product.id])[product.id]
        assert summary["search_page_url"] == "https://air.1688.com/search"
        assert summary["found"] == 698
        assert summary["top1"]["title"] == "Product 1"
        assert [item["rank"] for item in summary["products"]] == [1, 2, 3]


def test_force_image_search_resets_failed_task():
    with create_session() as db:
        user = create_user(db, "image-search-reset")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-image-search-reset", [incoming("624", 1)]),
        )
        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "624"))
        assert product is not None
        product.image_status = "ready"
        task = enqueue_temu_image_search(
            db,
            product,
            owner_id=user.id,
            group_id=group.id,
            source="image_ready",
        )
        assert task is not None
        db.flush()
        task.status = "failed"
        task.remote_stage = "product"
        task.upload_task_id = "upload-old"
        task.product_task_id = "product-old"
        task.attempts = 5
        task.last_error_code = "RATE_LIMITED"
        task.last_error = "limited"

        reset = enqueue_temu_image_search(
            db,
            product,
            owner_id=user.id,
            group_id=group.id,
            source="manual",
            force=True,
        )
        assert reset is task
        assert task.status == "pending"
        assert task.source == "manual"
        assert task.remote_stage is None
        assert task.upload_task_id is None
        assert task.product_task_id is None
        assert task.attempts == 0
        assert task.last_error_code is None
        assert task.last_error is None


def test_image_search_uses_public_cached_image_and_falls_back(tmp_path: Path):
    with create_session() as db:
        user = create_user(db, "image-search-public-cache")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-image-search-public-cache", [incoming("625", 1)]),
        )
        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "625"))
        assert product is not None
        product.cached_image_name = "temu-625-test image.jpg"
        cached_path = tmp_path / product.cached_image_name
        cached_path.write_bytes(b"image")
        config = image_search_config(db)
        config.public_image_base_url = "https://products.example.com"

        image_url, source = resolve_search_image_url(product, config, tmp_path)
        assert image_url == "https://products.example.com/images/temu-625-test%20image.jpg"
        assert source == "cache"

        cached_path.unlink()
        image_url, source = resolve_search_image_url(product, config, tmp_path)
        assert image_url == product.image_url
        assert source == "temu"


def test_image_search_queue_pause_preserves_two_stage_checkpoint():
    with create_session() as db:
        user = create_user(db, "image-search-queue")
        group, _ = get_or_create_temu_group(db, user.id, "A")
        ingest_temu_batch(
            db,
            user,
            batch(group.id, "temu-image-search-queue", [incoming("626", 1)]),
        )
        product = db.scalar(select(TemuProduct).where(TemuProduct.temu_product_id == "626"))
        assert product is not None
        product.image_status = "ready"
        task = enqueue_temu_image_search(
            db,
            product,
            owner_id=user.id,
            group_id=group.id,
            source="manual",
        )
        assert task is not None
        db.flush()
        task.status = "processing"
        task.remote_stage = "product"
        task.upload_task_id = "upload-626"
        task.product_task_id = "product-626"
        task.request_image_url = "https://products.example.com/images/626.jpg"
        task.request_image_source = "cache"
        db.commit()

        config, interrupted = set_image_search_queue_state(db, "paused")
        assert config.queue_state == "paused"
        assert interrupted == 1
        assert task.status == "pending"
        assert task.remote_stage == "product"
        assert task.upload_task_id == "upload-626"
        assert task.product_task_id == "product-626"
        assert claim_temu_image_search(db) is None

        set_image_search_queue_state(db, "running")
        claimed = claim_temu_image_search(db)
        assert claimed is not None
        assert claimed.remote_stage == "product"
        assert claimed.upload_task_id == "upload-626"
        assert claimed.product_task_id == "product-626"

        config, interrupted = set_image_search_queue_state(db, "stopped")
        assert config.queue_state == "stopped"
        assert interrupted == 1
        assert task.status == "pending"
        assert task.upload_task_id == "upload-626"
        assert task.product_task_id == "product-626"


def test_cancel_remote_tasks_calls_product_before_upload():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(
            200,
            json={"task_id": request.url.path.rsplit("/", 1)[-1], "status": "cancelled"},
        )

    async def run_cancel() -> list[str]:
        original_client = httpx.AsyncClient

        class MockAsyncClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                super().__init__(transport=httpx.MockTransport(handler))

        httpx.AsyncClient = MockAsyncClient
        try:
            return await cancel_remote_tasks(
                base_url="https://search.example.com:9443",
                api_key="secret",
                timeout_seconds=30,
                upload_task_id="upload-1",
                product_task_id="product-1",
            )
        finally:
            httpx.AsyncClient = original_client

    assert asyncio.run(run_cancel()) == []
    assert calls == [
        "DELETE /api/v1/product-tasks/product-1",
        "DELETE /api/v1/upload-tasks/upload-1",
    ]


def test_cancel_remote_tasks_reports_failure_and_continues():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "product-tasks" in request.url.path:
            return httpx.Response(
                503,
                json={
                    "detail": {
                        "code": "COOKIE_UNAVAILABLE",
                        "message": "Cookie unavailable",
                    }
                },
            )
        return httpx.Response(
            200,
            json={"task_id": "upload-2", "status": "cancelled"},
        )

    async def run_cancel() -> list[str]:
        original_client = httpx.AsyncClient

        class MockAsyncClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                super().__init__(transport=httpx.MockTransport(handler))

        httpx.AsyncClient = MockAsyncClient
        try:
            return await cancel_remote_tasks(
                base_url="https://search.example.com",
                api_key="secret",
                timeout_seconds=30,
                upload_task_id="upload-2",
                product_task_id="product-2",
            )
        finally:
            httpx.AsyncClient = original_client

    failures = asyncio.run(run_cancel())
    assert len(failures) == 1
    assert "product-tasks/product-2" in failures[0]
    assert calls == [
        "/api/v1/product-tasks/product-2",
        "/api/v1/upload-tasks/upload-2",
    ]


def test_temu_favorites_are_owner_scoped_and_export_top1():
    with create_session() as db:
        owner = create_user(db, "favorite-owner")
        other = create_user(db, "favorite-other")
        group = TemuProductGroup(
            owner_id=owner.id,
            name="收藏分组",
            normalized_name="收藏分组",
        )
        db.add(group)
        db.flush()
        product = TemuProduct(
            temu_product_id="990001",
            title="收藏产品",
            product_url="https://www.temu.com/goods.html?goods_id=990001",
            image_url="https://img.example.com/favorite.jpg",
            daily_sales=12,
            weekly_sales=34,
            monthly_sales=0,
            total_sales=100,
            is_ad=False,
        )
        db.add(product)
        db.flush()
        db.add(TemuGroupMembership(owner_id=owner.id, product_id=product.id, group_id=group.id))
        db.commit()

        assert set_temu_favorite(db, owner.id, product.id, True) is True
        assert set_temu_favorite(db, owner.id, product.id, True) is True
        assert db.scalar(select(TemuProductFavorite).where(TemuProductFavorite.owner_id == owner.id))
        try:
            set_temu_favorite(db, other.id, product.id, True)
            assert False, "other owner must not favorite inaccessible product"
        except LookupError:
            pass

        content, total = build_favorites_workbook(
            db, owner.id, TemuFilters(include_ads=True), "daily_sales", "desc"
        )
        assert total == 1
        workbook = load_workbook(BytesIO(content), data_only=False)
        sheet = workbook.active
        assert sheet["A2"].value == "收藏产品"
        assert str(sheet["B2"].value).startswith('=IFERROR(IMAGE(')
        assert sheet["H2"].value in {None, ""}

        assert set_temu_favorite(db, owner.id, product.id, False) is False
        assert db.scalar(select(TemuProductFavorite).where(TemuProductFavorite.owner_id == owner.id)) is None


def test_temu_remove_membership_preserves_shared_product_and_hard_deletes_orphan():
    with create_session() as db:
        owner = create_user(db, "remove-owner")
        other = create_user(db, "remove-other")
        first_group = TemuProductGroup(owner_id=owner.id, name="第一组", normalized_name="第一组")
        second_group = TemuProductGroup(owner_id=owner.id, name="第二组", normalized_name="第二组")
        other_group = TemuProductGroup(owner_id=other.id, name="其他组", normalized_name="其他组")
        product = TemuProduct(
            temu_product_id="remove-1",
            title="待删除商品",
            product_url="https://www.temu.com/remove-1",
            image_url="https://img.example.com/remove-1.jpg",
            daily_sales=1,
            weekly_sales=2,
            monthly_sales=3,
            total_sales=4,
            is_ad=False,
        )
        db.add_all([first_group, second_group, other_group, product])
        db.flush()
        db.add_all([
            TemuGroupMembership(owner_id=owner.id, product_id=product.id, group_id=first_group.id),
            TemuGroupMembership(owner_id=owner.id, product_id=product.id, group_id=second_group.id),
            TemuGroupMembership(owner_id=other.id, product_id=product.id, group_id=other_group.id),
            TemuProductFavorite(owner_id=owner.id, product_id=product.id),
            TemuImageDownloadTask(product_id=product.id, image_url=product.image_url),
            TemuImageSearchTask(product_id=product.id, image_url=product.image_url),
        ])
        db.commit()

        assert remove_temu_product_memberships(db, owner.id, product.id, group_id=first_group.id) is False
        assert db.get(TemuProduct, product.id) is not None
        assert db.scalar(select(TemuGroupMembership).where(TemuGroupMembership.group_id == first_group.id)) is None
        assert db.scalar(select(TemuGroupMembership).where(TemuGroupMembership.group_id == second_group.id)) is not None

        assert remove_temu_product_memberships(db, owner.id, product.id, group_id=second_group.id) is False
        assert db.get(TemuProduct, product.id) is not None

        assert remove_temu_group(db, other_group) == 1
        assert db.get(TemuProduct, product.id) is None
        assert db.scalar(select(TemuProductFavorite).where(TemuProductFavorite.product_id == product.id)) is None
        assert db.scalar(select(TemuImageDownloadTask).where(TemuImageDownloadTask.product_id == product.id)) is None
        assert db.scalar(select(TemuImageSearchTask).where(TemuImageSearchTask.product_id == product.id)) is None


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
