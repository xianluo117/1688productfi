from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .models import ImageEmbeddingConfig, Product, ProductImageEmbedding, SimilarityTask
from .similarity_services import (
    claim_task,
    generate_candidates,
    image_fingerprints,
    queue_control,
    request_embedding,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()


async def process_task(task_id: int) -> None:
    with SessionLocal() as db:
        task = db.get(SimilarityTask, task_id)
        if not task:
            return
        product = db.get(Product, task.product_id)
        config = db.get(ImageEmbeddingConfig, task.config_id)
        if not product or not config or not product.cached_image_name:
            task.status = "failed"
            task.last_error = "商品、模型配置或缓存图片不存在"
            task.finished_at = datetime.now(timezone.utc)
            task.locked_at = None
            db.commit()
            return
        image_path = settings.image_storage_path / product.cached_image_name

    try:
        content_hash, phash, content = await asyncio.to_thread(image_fingerprints, image_path)
        with SessionLocal() as db:
            task = db.get(SimilarityTask, task_id)
            if not task:
                return
            product = db.get(Product, task.product_id)
            config = db.get(ImageEmbeddingConfig, task.config_id)
            if not product or not config:
                return
            existing = db.scalar(
                select(ProductImageEmbedding).where(
                    ProductImageEmbedding.product_id == product.id,
                    ProductImageEmbedding.config_id == config.id,
                    ProductImageEmbedding.image_content_hash == content_hash,
                )
            )
            product.image_content_hash = content_hash
            product.image_phash = phash
            task.image_content_hash = content_hash
            if existing:
                product.similarity_status = "ready"
                task.status = "completed"
                task.finished_at = datetime.now(timezone.utc)
                task.locked_at = None
                task.last_error = None
                db.commit()
                return

        vector = await request_embedding(config, product, content, settings)
        with SessionLocal() as db:
            task = db.get(SimilarityTask, task_id)
            if not task:
                return
            product = db.get(Product, task.product_id)
            config = db.get(ImageEmbeddingConfig, task.config_id)
            if not product or not config:
                return
            embedding = ProductImageEmbedding(
                product_id=product.id,
                config_id=config.id,
                image_content_hash=content_hash,
                phash=phash,
                vector=vector,
                source_dimension=config.output_dimension,
            )
            db.add(embedding)
            db.flush()
            generate_candidates(db, embedding, config)
            product.similarity_status = "ready"
            task.status = "completed"
            task.finished_at = datetime.now(timezone.utc)
            task.locked_at = None
            task.last_error = None
            db.commit()
    except Exception as error:
        logger.exception("Similarity task %s failed", task_id)
        with SessionLocal() as db:
            task = db.get(SimilarityTask, task_id)
            if not task:
                return
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
    logger.info("Similarity worker started with dynamic concurrency")
    running: set[asyncio.Task[None]] = set()
    while True:
        running = {task for task in running if not task.done()}
        with SessionLocal() as db:
            control = queue_control(db)
            state = control.state
            concurrency = max(1, min(32, control.concurrency))

        if state == "running":
            while len(running) < concurrency:
                with SessionLocal() as db:
                    task = claim_task(db)
                    task_id = task.id if task else None
                if task_id is None:
                    break
                running.add(asyncio.create_task(process_task(task_id)))
        elif state == "stopped" and not running:
            await asyncio.sleep(1)
            continue

        if running:
            await asyncio.wait(running, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
        else:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(run())
