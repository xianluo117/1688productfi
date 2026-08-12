from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .image_cache import normalize_temu_image_url
from .models import (
    TemuGroupMembership,
    TemuImageDownloadTask,
    TemuProduct,
    TemuProductGroup,
)

ACTIVE_DOWNLOAD_STATUSES = ("pending", "processing")


def cached_image_exists(product: TemuProduct, image_storage_path: Path) -> bool:
    return bool(
        product.cached_image_name
        and (image_storage_path / product.cached_image_name).is_file()
    )


def enqueue_temu_image_download(
    db: Session,
    product: TemuProduct,
    *,
    owner_id: int | None,
    group_id: int | None,
    source: str,
    force: bool = False,
) -> TemuImageDownloadTask | None:
    if not product.image_url:
        product.image_status = "missing"
        return None
    normalized_url = normalize_temu_image_url(product.image_url)
    product.image_url = normalized_url
    existing = db.scalar(
        select(TemuImageDownloadTask).where(
            TemuImageDownloadTask.product_id == product.id,
            TemuImageDownloadTask.image_url == normalized_url,
        )
    )
    if existing:
        if force or existing.status in {"failed", "cancelled"}:
            existing.owner_id = owner_id
            existing.group_id = group_id
            existing.source = source
            existing.status = "pending"
            existing.attempts = 0
            existing.last_error = None
            existing.available_at = datetime.now(timezone.utc)
            existing.locked_at = None
            existing.started_at = None
            existing.finished_at = None
            product.image_status = "pending"
        return existing
    task = TemuImageDownloadTask(
        product_id=product.id,
        owner_id=owner_id,
        group_id=group_id,
        image_url=normalized_url,
        source=source,
    )
    product.image_status = "pending"
    db.add(task)
    return task


def scope_temu_products(
    db: Session,
    owner_id: int,
    group_ids: list[int],
) -> list[tuple[TemuProduct, int | None]]:
    if group_ids:
        owned_group_ids = set(
            db.scalars(
                select(TemuProductGroup.id).where(
                    TemuProductGroup.owner_id == owner_id,
                    TemuProductGroup.id.in_(group_ids),
                )
            )
        )
        if owned_group_ids != set(group_ids):
            raise ValueError("存在不属于目标用户的 Temu 分组")
        rows = db.execute(
            select(TemuProduct, TemuGroupMembership.group_id)
            .join(TemuGroupMembership, TemuGroupMembership.product_id == TemuProduct.id)
            .where(
                TemuGroupMembership.owner_id == owner_id,
                TemuGroupMembership.group_id.in_(group_ids),
            )
            .order_by(TemuProduct.id.asc())
        ).all()
    else:
        rows = db.execute(
            select(TemuProduct, TemuGroupMembership.group_id)
            .join(TemuGroupMembership, TemuGroupMembership.product_id == TemuProduct.id)
            .where(TemuGroupMembership.owner_id == owner_id)
            .order_by(TemuProduct.id.asc())
        ).all()
    deduplicated: dict[int, tuple[TemuProduct, int | None]] = {}
    for product, group_id in rows:
        deduplicated.setdefault(product.id, (product, group_id))
    return list(deduplicated.values())


def enqueue_temu_image_scope(
    db: Session,
    owner_id: int,
    group_ids: list[int],
    image_storage_path: Path,
) -> int:
    count = 0
    for product, group_id in scope_temu_products(db, owner_id, group_ids):
        if not product.image_url:
            product.image_status = "missing"
            continue
        if product.image_status == "ready" and cached_image_exists(product, image_storage_path):
            continue
        if product.cached_image_name and not cached_image_exists(product, image_storage_path):
            product.cached_image_name = None
            product.image_content_hash = None
            product.image_phash = None
        task = enqueue_temu_image_download(
            db,
            product,
            owner_id=owner_id,
            group_id=group_id,
            source="manual",
            force=True,
        )
        if task:
            count += 1
    return count


def claim_temu_image_download(db: Session) -> TemuImageDownloadTask | None:
    now = datetime.now(timezone.utc)
    task = db.scalar(
        select(TemuImageDownloadTask)
        .where(
            TemuImageDownloadTask.status == "pending",
            TemuImageDownloadTask.available_at <= now,
        )
        .order_by(TemuImageDownloadTask.id.asc())
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


def temu_image_download_stats(
    db: Session,
    owner_id: int,
    group_ids: list[int],
    image_storage_path: Path,
) -> dict[str, int]:
    rows = scope_temu_products(db, owner_id, group_ids)
    product_ids = [product.id for product, _ in rows]
    total = len(product_ids)
    ready = sum(
        1 for product, _ in rows
        if product.image_status == "ready" and cached_image_exists(product, image_storage_path)
    )
    missing = sum(1 for product, _ in rows if not product.image_url)
    counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
    if product_ids:
        for status, count in db.execute(
            select(TemuImageDownloadTask.status, func.count(TemuImageDownloadTask.id))
            .where(TemuImageDownloadTask.product_id.in_(product_ids))
            .group_by(TemuImageDownloadTask.status)
        ):
            if status in counts:
                counts[status] = int(count)
    return {
        "total": total,
        "ready": ready,
        "missing": missing,
        "waiting": max(0, total - ready - missing),
        **counts,
    }
