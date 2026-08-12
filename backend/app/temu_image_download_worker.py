from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .database import SessionLocal
from .image_cache import cache_product_image, normalize_temu_image_url
from .models import TemuImageDownloadTask, TemuProduct
from .similarity_services import enqueue_temu_product, queue_control
from .temu_image_download_services import claim_temu_image_download

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()


async def process_download(task_id: int) -> None:
    try:
        with SessionLocal() as db:
            task = db.get(TemuImageDownloadTask, task_id)
            if not task:
                return
            product = db.get(TemuProduct, task.product_id)
            if not product:
                raise LookupError("Temu 商品不存在")
            normalized_url = normalize_temu_image_url(task.image_url)
            external_id = product.temu_product_id

        filename = await cache_product_image(
            external_id,
            normalized_url,
            settings,
            namespace="temu",
        )

        with SessionLocal() as db:
            task = db.get(TemuImageDownloadTask, task_id)
            if not task:
                return
            product = db.get(TemuProduct, task.product_id)
            if not product:
                raise LookupError("Temu 商品不存在")
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
            task.status = "completed"
            task.last_error = None
            task.locked_at = None
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as error:
        logger.exception("Temu image download task %s failed: %s", task_id, error)
        with SessionLocal() as db:
            task = db.get(TemuImageDownloadTask, task_id)
            if not task:
                return
            product = db.get(TemuProduct, task.product_id)
            if product:
                product.image_status = "failed"
            task.last_error = f"{type(error).__name__}: {error}"[:4000]
            task.locked_at = None
            if task.attempts >= 5:
                task.status = "failed"
                task.finished_at = datetime.now(timezone.utc)
            else:
                task.status = "pending"
                task.available_at = datetime.now(timezone.utc) + timedelta(
                    seconds=min(300, 2 ** task.attempts * 5)
                )
            db.commit()


async def run() -> None:
    running: set[asyncio.Task] = set()
    while True:
        running = {task for task in running if not task.done()}
        while len(running) < 4:
            with SessionLocal() as db:
                task = claim_temu_image_download(db)
            if task is None:
                break
            running.add(asyncio.create_task(process_download(task.id)))
        await asyncio.sleep(1 if running else 3)


if __name__ == "__main__":
    asyncio.run(run())
