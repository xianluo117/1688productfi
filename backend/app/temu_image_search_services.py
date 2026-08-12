from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .image_cache import normalize_temu_image_url
from .models import (
    TemuGroupMembership,
    TemuImageSearchConfig,
    TemuImageSearchResult,
    TemuImageSearchResultItem,
    TemuImageSearchTask,
    TemuProduct,
    TemuProductGroup,
)
from .similarity_services import decrypt_api_key, encrypt_api_key, mask_api_key
from .temu_image_download_services import scope_temu_products


def image_search_config(db: Session, create: bool = True) -> TemuImageSearchConfig | None:
    config = db.get(TemuImageSearchConfig, 1)
    if config is None and create:
        config = TemuImageSearchConfig(id=1)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def config_payload(config: TemuImageSearchConfig, settings: Settings) -> dict[str, Any]:
    api_key = decrypt_api_key(config.api_key_encrypted, settings)
    return {
        "id": config.id,
        "base_url": config.base_url,
        "api_key_masked": mask_api_key(api_key),
        "api_key_configured": bool(api_key),
        "auto_search_enabled": config.auto_search_enabled,
        "timeout_seconds": config.timeout_seconds,
        "poll_interval_seconds": config.poll_interval_seconds,
        "task_timeout_seconds": config.task_timeout_seconds,
        "updated_at": config.updated_at,
    }


def update_image_search_config(
    db: Session,
    config: TemuImageSearchConfig,
    *,
    base_url: str,
    api_key: str | None,
    auto_search_enabled: bool,
    timeout_seconds: int,
    poll_interval_seconds: int,
    task_timeout_seconds: int,
    updated_by: int,
    settings: Settings,
) -> TemuImageSearchConfig:
    config.base_url = base_url.rstrip("/")
    if api_key is not None:
        config.api_key_encrypted = encrypt_api_key(api_key, settings)
    config.auto_search_enabled = auto_search_enabled
    config.timeout_seconds = timeout_seconds
    config.poll_interval_seconds = poll_interval_seconds
    config.task_timeout_seconds = task_timeout_seconds
    config.updated_by = updated_by
    db.commit()
    db.refresh(config)
    return config


def reset_image_search_task(task: TemuImageSearchTask, *, source: str | None = None) -> None:
    if source:
        task.source = source
    task.status = "pending"
    task.remote_task_id = None
    task.attempts = 0
    task.last_error_code = None
    task.last_error = None
    task.available_at = datetime.now(timezone.utc)
    task.locked_at = None
    task.started_at = None
    task.finished_at = None


def enqueue_temu_image_search(
    db: Session,
    product: TemuProduct,
    *,
    owner_id: int | None,
    group_id: int | None,
    source: str,
    force: bool = False,
) -> TemuImageSearchTask | None:
    if product.image_status != "ready" or not product.image_url:
        return None
    normalized_url = normalize_temu_image_url(product.image_url)
    product.image_url = normalized_url
    existing = db.scalar(
        select(TemuImageSearchTask).where(
            TemuImageSearchTask.product_id == product.id,
            TemuImageSearchTask.image_url == normalized_url,
        )
    )
    if existing:
        if force or existing.status in {"failed", "cancelled"}:
            existing.owner_id = owner_id
            existing.group_id = group_id
            reset_image_search_task(existing, source=source)
        return existing
    task = TemuImageSearchTask(
        product_id=product.id,
        owner_id=owner_id,
        group_id=group_id,
        image_url=normalized_url,
        source=source,
    )
    db.add(task)
    return task


def enqueue_temu_image_search_scope(
    db: Session,
    owner_id: int,
    group_ids: list[int],
    *,
    force: bool = True,
) -> int:
    count = 0
    for product, group_id in scope_temu_products(db, owner_id, group_ids):
        task = enqueue_temu_image_search(
            db,
            product,
            owner_id=owner_id,
            group_id=group_id,
            source="manual",
            force=force,
        )
        if task:
            count += 1
    return count


def claim_temu_image_search(db: Session) -> TemuImageSearchTask | None:
    now = datetime.now(timezone.utc)
    stale = now - timedelta(minutes=10)
    task = db.scalar(
        select(TemuImageSearchTask)
        .where(
            TemuImageSearchTask.available_at <= now,
            or_(
                TemuImageSearchTask.status == "pending",
                (
                    (TemuImageSearchTask.status == "processing")
                    & (TemuImageSearchTask.locked_at < stale)
                ),
            ),
        )
        .order_by(TemuImageSearchTask.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if task:
        task.status = "processing"
        task.attempts += 1
        task.locked_at = now
        task.started_at = now
        db.commit()
        db.refresh(task)
    return task


def replace_image_search_result(
    db: Session,
    task: TemuImageSearchTask,
    payload: dict[str, Any],
) -> TemuImageSearchResult:
    existing = db.scalar(
        select(TemuImageSearchResult).where(TemuImageSearchResult.task_id == task.id)
    )
    if existing:
        db.execute(
            delete(TemuImageSearchResultItem).where(
                TemuImageSearchResultItem.result_id == existing.id
            )
        )
        result = existing
    else:
        result = TemuImageSearchResult(task_id=task.id, product_id=task.product_id)
        db.add(result)
        db.flush()
    result.search_page_url = payload.get("search_page_url")
    result.image_id = payload.get("image_id")
    found = payload.get("found")
    result.found = int(found) if found is not None else None
    for rank, item in enumerate((payload.get("products") or [])[:3], start=1):
        sale_quantity = item.get("sale_quantity")
        db.add(
            TemuImageSearchResultItem(
                result_id=result.id,
                rank=rank,
                image_url=item.get("image"),
                title=item.get("title"),
                price=str(item["price"]) if item.get("price") is not None else None,
                sale_quantity=(
                    str(sale_quantity) if sale_quantity is not None else None
                ),
                product_url=item.get("product_url"),
            )
        )
    return result


def image_search_stats(
    db: Session,
    owner_id: int,
    group_ids: list[int],
) -> dict[str, int]:
    rows = scope_temu_products(db, owner_id, group_ids)
    product_ids = [product.id for product, _ in rows]
    searchable = sum(
        1
        for product, _ in rows
        if product.image_status == "ready" and bool(product.image_url)
    )
    counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
    succeeded_products = 0
    if product_ids:
        for status, count in db.execute(
            select(TemuImageSearchTask.status, func.count(TemuImageSearchTask.id))
            .where(TemuImageSearchTask.product_id.in_(product_ids))
            .group_by(TemuImageSearchTask.status)
        ):
            if status in counts:
                counts[status] = int(count)
        succeeded_products = int(
            db.scalar(
                select(func.count(func.distinct(TemuImageSearchResult.product_id))).where(
                    TemuImageSearchResult.product_id.in_(product_ids)
                )
            )
            or 0
        )
    return {
        "total": len(product_ids),
        "searchable": searchable,
        "succeeded_products": succeeded_products,
        "waiting_products": max(0, searchable - succeeded_products),
        **counts,
    }


def image_search_summaries(
    db: Session,
    product_ids: list[int],
) -> dict[int, dict[str, Any]]:
    if not product_ids:
        return {}
    tasks = list(
        db.scalars(
            select(TemuImageSearchTask)
            .where(TemuImageSearchTask.product_id.in_(product_ids))
            .order_by(TemuImageSearchTask.product_id.asc(), TemuImageSearchTask.id.desc())
        )
    )
    latest: dict[int, TemuImageSearchTask] = {}
    for task in tasks:
        latest.setdefault(task.product_id, task)
    task_ids = [task.id for task in latest.values()]
    results = {
        result.task_id: result
        for result in db.scalars(
            select(TemuImageSearchResult).where(TemuImageSearchResult.task_id.in_(task_ids))
        )
    }
    result_ids = [result.id for result in results.values()]
    items_by_result: dict[int, list[TemuImageSearchResultItem]] = {}
    if result_ids:
        for item in db.scalars(
            select(TemuImageSearchResultItem)
            .where(TemuImageSearchResultItem.result_id.in_(result_ids))
            .order_by(TemuImageSearchResultItem.result_id, TemuImageSearchResultItem.rank)
        ):
            items_by_result.setdefault(item.result_id, []).append(item)
    summaries: dict[int, dict[str, Any]] = {}
    for product_id, task in latest.items():
        result = results.get(task.id)
        items = items_by_result.get(result.id, []) if result else []
        products = [
            {
                "rank": item.rank,
                "image_url": item.image_url,
                "title": item.title,
                "price": item.price,
                "sale_quantity": item.sale_quantity,
                "product_url": item.product_url,
            }
            for item in items
        ]
        summaries[product_id] = {
            "status": task.status,
            "search_page_url": result.search_page_url if result else None,
            "image_id": result.image_id if result else None,
            "found": result.found if result else None,
            "top1": products[0] if products else None,
            "products": products,
            "error_code": task.last_error_code,
            "error_message": task.last_error,
            "finished_at": task.finished_at,
        }
    return summaries


def configured_search_api_key(
    config: TemuImageSearchConfig,
    settings: Settings,
) -> str:
    api_key = decrypt_api_key(config.api_key_encrypted, settings)
    if not config.base_url or not api_key:
        raise ValueError("1688 图搜 API 地址或 API Key 尚未配置")
    return api_key
