from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import imagehash
from cryptography.fernet import Fernet
from PIL import Image
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    AdminUser,
    ImageEmbeddingConfig,
    Product,
    ProductGroup,
    ProductGroupMembership,
    ProductImageEmbedding,
    ProductSimilarityCandidate,
    ProductSimilarityCluster,
    ProductSimilarityClusterMember,
    SimilarityAuditLog,
    SimilarityTask,
)

VECTOR_STORAGE_DIMENSION = 1536


def _fernet(settings: Settings) -> Fernet:
    digest = hashlib.sha256(settings.resolved_ai_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(value: str | None, settings: Settings) -> str | None:
    if not value:
        return None
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_api_key(value: str | None, settings: Settings) -> str | None:
    if not value:
        return None
    return _fernet(settings).decrypt(value.encode("ascii")).decode("utf-8")


def mask_api_key(value: str | None) -> str | None:
    if not value:
        return None
    return f"{value[:3]}***{value[-3:]}" if len(value) > 8 else "***"


def active_config(db: Session) -> ImageEmbeddingConfig | None:
    return db.scalar(
        select(ImageEmbeddingConfig)
        .where(ImageEmbeddingConfig.is_active.is_(True))
        .order_by(ImageEmbeddingConfig.id.desc())
    )


def enqueue_product(db: Session, product: Product, config: ImageEmbeddingConfig | None = None) -> SimilarityTask | None:
    if not product.cached_image_name or product.image_status != "ready":
        return None
    config = config or active_config(db)
    if config is None:
        product.similarity_status = "disabled"
        return None
    image_hash = product.image_content_hash or f"pending:{product.cached_image_name}"
    existing = db.scalar(
        select(SimilarityTask).where(
            SimilarityTask.product_id == product.id,
            SimilarityTask.config_id == config.id,
            SimilarityTask.image_content_hash == image_hash,
        )
    )
    if existing:
        if existing.status == "completed" and not db.scalar(
            select(ProductImageEmbedding).where(
                ProductImageEmbedding.product_id == product.id,
                ProductImageEmbedding.config_id == config.id,
                ProductImageEmbedding.image_content_hash == image_hash,
            )
        ):
            existing.status = "pending"
            existing.attempts = 0
            existing.last_error = None
            existing.available_at = datetime.now(timezone.utc)
            existing.finished_at = None
        return existing
    task = SimilarityTask(product_id=product.id, config_id=config.id, image_content_hash=image_hash)
    product.similarity_status = "pending"
    db.add(task)
    return task


def enqueue_all_products(db: Session, config: ImageEmbeddingConfig, force: bool = False) -> int:
    products = db.scalars(
        select(Product).where(Product.cached_image_name.is_not(None), Product.image_status == "ready")
    ).all()
    count = 0
    for product in products:
        task = enqueue_product(db, product, config)
        if not task:
            continue
        if force:
            task.status = "pending"
            task.attempts = 0
            task.last_error = None
            task.available_at = datetime.now(timezone.utc)
            task.locked_at = None
            task.finished_at = None
            product.similarity_status = "pending"
        count += 1
    return count


def image_fingerprints(path: Path) -> tuple[str, str, bytes]:
    content = path.read_bytes()
    content_hash = hashlib.sha256(content).hexdigest()
    with Image.open(path) as image:
        phash = str(imagehash.phash(image.convert("RGB")))
    return content_hash, phash, content


def phash_distance(left: str, right: str) -> int:
    return imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right)


def _replace_template(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_template(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_template(item, replacements) for item in value]
    if not isinstance(value, str):
        return value
    if value in replacements:
        return replacements[value]
    result = value
    for placeholder, replacement in replacements.items():
        if isinstance(replacement, str):
            result = result.replace(placeholder, replacement)
    return result


def build_embedding_request(config: ImageEmbeddingConfig, product: Product, content: bytes) -> dict[str, Any]:
    mime = "image/png" if product.cached_image_name and product.cached_image_name.lower().endswith(".png") else "image/jpeg"
    image_base64 = base64.b64encode(content).decode("ascii")
    replacements = {
        "{{model}}": config.model_name,
        "{{image_base64}}": image_base64,
        "{{image_data_url}}": f"data:{mime};base64,{image_base64}",
        "{{image_url}}": product.original_image_url or "",
        "{{product_id}}": product.offer_id,
    }
    return _replace_template(config.request_template, replacements)


def extract_response_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError(f"response path stopped at {part}")
    return current


def normalize_vector(raw: Any, expected_dimension: int) -> list[float]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("embedding response is not a non-empty list")
    vector = [float(value) for value in raw]
    if len(vector) != expected_dimension:
        raise ValueError(f"embedding dimension {len(vector)} does not match configured {expected_dimension}")
    if len(vector) > VECTOR_STORAGE_DIMENSION:
        raise ValueError(f"embedding dimension exceeds storage limit {VECTOR_STORAGE_DIMENSION}")
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        raise ValueError("embedding vector norm is zero")
    normalized = [value / norm for value in vector]
    return normalized + [0.0] * (VECTOR_STORAGE_DIMENSION - len(normalized))


def embedding_endpoint_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/embeddings"):
        return cleaned
    return f"{cleaned}/embeddings"


async def request_embedding(
    config: ImageEmbeddingConfig,
    product: Product,
    content: bytes,
    settings: Settings,
) -> list[float]:
    headers = {"Content-Type": "application/json"}
    api_key = decrypt_api_key(config.api_key_encrypted, settings)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response = await client.post(
            embedding_endpoint_url(config.base_url),
            headers=headers,
            json=build_embedding_request(config, product, content),
        )
        response.raise_for_status()
    raw = extract_response_path(response.json(), config.response_path)
    return normalize_vector(raw, config.output_dimension)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right, strict=True)))


def representative_sort_key(product: Product) -> tuple[int, float, datetime]:
    return (
        product.total_pieces,
        float(product.daily_orders),
        product.last_collected_at,
    )


def recalculate_cluster_representative(db: Session, cluster_id: int) -> ProductSimilarityCluster | None:
    cluster = db.get(ProductSimilarityCluster, cluster_id)
    if not cluster:
        return None
    products = db.scalars(
        select(Product)
        .join(ProductSimilarityClusterMember, ProductSimilarityClusterMember.product_id == Product.id)
        .where(ProductSimilarityClusterMember.cluster_id == cluster_id)
    ).all()
    if not products:
        db.delete(cluster)
        return None
    cluster.representative_product_id = max(products, key=representative_sort_key).id
    return cluster


def _cluster_for_product(db: Session, product_id: int) -> ProductSimilarityCluster | None:
    return db.scalar(
        select(ProductSimilarityCluster)
        .join(ProductSimilarityClusterMember)
        .where(ProductSimilarityClusterMember.product_id == product_id)
    )


def merge_products(
    db: Session,
    left_product_id: int,
    right_product_id: int,
    source: str,
    confidence: float | None,
    actor_id: int | None = None,
    candidate_id: int | None = None,
) -> ProductSimilarityCluster:
    left_cluster = _cluster_for_product(db, left_product_id)
    right_cluster = _cluster_for_product(db, right_product_id)
    if left_cluster and right_cluster and left_cluster.id == right_cluster.id:
        return left_cluster

    if left_cluster is None:
        left_cluster = ProductSimilarityCluster(representative_product_id=left_product_id)
        db.add(left_cluster)
        db.flush()
        db.add(ProductSimilarityClusterMember(
            cluster_id=left_cluster.id,
            product_id=left_product_id,
            decision_source=source,
            confidence=confidence,
            added_by=actor_id,
        ))
    if right_cluster is None:
        db.add(ProductSimilarityClusterMember(
            cluster_id=left_cluster.id,
            product_id=right_product_id,
            decision_source=source,
            confidence=confidence,
            added_by=actor_id,
        ))
    else:
        members = db.scalars(
            select(ProductSimilarityClusterMember).where(ProductSimilarityClusterMember.cluster_id == right_cluster.id)
        ).all()
        for member in members:
            member.cluster_id = left_cluster.id
        db.flush()
        db.delete(right_cluster)

    db.flush()
    recalculate_cluster_representative(db, left_cluster.id)
    db.add(SimilarityAuditLog(
        action="merge",
        actor_id=actor_id,
        candidate_id=candidate_id,
        cluster_id=left_cluster.id,
        payload={"left_product_id": left_product_id, "right_product_id": right_product_id, "source": source},
    ))
    return left_cluster


def cluster_consistent(db: Session, product_id: int, target_product_id: int, config: ImageEmbeddingConfig) -> bool:
    cluster = _cluster_for_product(db, target_product_id)
    if cluster is None:
        return True
    member_ids = db.scalars(
        select(ProductSimilarityClusterMember.product_id).where(
            ProductSimilarityClusterMember.cluster_id == cluster.id
        )
    ).all()
    if len(member_ids) <= 1:
        return True
    accepted_links = db.scalar(
        select(func.count(ProductSimilarityCandidate.id)).where(
            ProductSimilarityCandidate.status.in_(("approved", "auto_merged")),
            or_(
                and_(ProductSimilarityCandidate.left_product_id == product_id, ProductSimilarityCandidate.right_product_id.in_(member_ids)),
                and_(ProductSimilarityCandidate.right_product_id == product_id, ProductSimilarityCandidate.left_product_id.in_(member_ids)),
            ),
            or_(
                ProductSimilarityCandidate.image_similarity >= config.auto_merge_threshold,
                ProductSimilarityCandidate.phash_distance <= config.phash_auto_distance,
            ),
        )
    )
    return int(accepted_links or 0) >= min(2, len(member_ids))


def upsert_candidate(
    db: Session,
    product_id: int,
    other_id: int,
    config: ImageEmbeddingConfig,
    phash_value: int | None,
    similarity: float | None,
) -> ProductSimilarityCandidate:
    left_id, right_id = sorted((product_id, other_id))
    candidate = db.scalar(
        select(ProductSimilarityCandidate).where(
            ProductSimilarityCandidate.left_product_id == left_id,
            ProductSimilarityCandidate.right_product_id == right_id,
            ProductSimilarityCandidate.config_id == config.id,
        )
    )
    if candidate is None:
        candidate = ProductSimilarityCandidate(
            left_product_id=left_id,
            right_product_id=right_id,
            config_id=config.id,
        )
        db.add(candidate)
    if candidate.status == "rejected":
        return candidate
    candidate.phash_distance = phash_value
    candidate.image_similarity = similarity
    strict = (phash_value is not None and phash_value <= config.phash_auto_distance) or (
        similarity is not None and similarity >= config.auto_merge_threshold
    )
    reviewable = strict or (similarity is not None and similarity >= config.review_threshold)
    if strict and config.auto_merge_enabled and cluster_consistent(db, product_id, other_id, config):
        candidate.status = "auto_merged"
        candidate.decision_source = "automatic"
        candidate.reviewed_at = datetime.now(timezone.utc)
        db.flush()
        merge_products(db, product_id, other_id, "automatic", similarity, candidate_id=candidate.id)
    elif reviewable:
        candidate.status = "pending"
    else:
        candidate.status = "ignored"
    return candidate


def generate_candidates(
    db: Session,
    embedding: ProductImageEmbedding,
    config: ImageEmbeddingConfig,
) -> int:
    product = db.get(Product, embedding.product_id)
    if not product:
        return 0
    base_filter = (
        ProductImageEmbedding.config_id == config.id,
        ProductImageEmbedding.product_id != product.id,
    )
    vector_neighbors = db.scalars(
        select(ProductImageEmbedding)
        .where(*base_filter)
        .order_by(ProductImageEmbedding.vector.cosine_distance(embedding.vector))
        .limit(config.top_k)
    ).all()
    all_phashes = db.scalars(select(ProductImageEmbedding).where(*base_filter)).all()
    candidate_embeddings = {other.id: other for other in vector_neighbors}
    for other in all_phashes:
        if phash_distance(embedding.phash, other.phash) <= config.phash_auto_distance:
            candidate_embeddings[other.id] = other

    scored: list[tuple[float, ProductImageEmbedding, int]] = []
    for other in candidate_embeddings.values():
        distance = phash_distance(embedding.phash, other.phash)
        similarity = cosine_similarity(list(embedding.vector), list(other.vector))
        if distance <= config.phash_auto_distance or similarity >= config.review_threshold:
            scored.append((similarity, other, distance))
    scored.sort(key=lambda item: item[0], reverse=True)
    count = 0
    for similarity, other, distance in scored:
        upsert_candidate(db, product.id, other.product_id, config, distance, similarity)
        count += 1
    return count


def claim_task(db: Session) -> SimilarityTask | None:
    now = datetime.now(timezone.utc)
    stale = now - timedelta(minutes=10)
    task = db.scalar(
        select(SimilarityTask)
        .where(
            SimilarityTask.available_at <= now,
            or_(
                SimilarityTask.status == "pending",
                and_(SimilarityTask.status == "processing", SimilarityTask.locked_at < stale),
            ),
        )
        .order_by(SimilarityTask.id)
        .with_for_update(skip_locked=True)
    )
    if task:
        task.status = "processing"
        task.locked_at = now
        task.attempts += 1
        db.commit()
    return task


def task_stats(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(SimilarityTask.status, func.count(SimilarityTask.id)).group_by(SimilarityTask.status)
    ).all()
    result = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
    result.update({status: int(count) for status, count in rows})
    result["review_pending"] = int(db.scalar(
        select(func.count(ProductSimilarityCandidate.id)).where(ProductSimilarityCandidate.status == "pending")
    ) or 0)
    return result


def product_sources(db: Session, product_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AdminUser.id, AdminUser.username, ProductGroup.id, ProductGroup.name)
        .join(ProductGroupMembership, ProductGroupMembership.owner_id == AdminUser.id)
        .join(ProductGroup, ProductGroup.id == ProductGroupMembership.group_id)
        .where(ProductGroupMembership.product_id == product_id)
        .distinct()
        .order_by(AdminUser.username, ProductGroup.name)
    ).all()
    return [
        {"user_id": user_id, "username": username, "group_id": group_id, "group_name": group_name}
        for user_id, username, group_id, group_name in rows
    ]
