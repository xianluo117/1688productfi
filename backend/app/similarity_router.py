from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .auth import get_current_user, require_admin
from .config import Settings, get_settings
from .database import get_db
from .models import (
    AdminUser,
    ImageEmbeddingConfig,
    Product,
    ProductSimilarityCandidate,
    ProductSimilarityCluster,
    ProductSimilarityClusterMember,
    SimilarityAuditLog,
    SimilarityTask,
)
from .schemas import (
    ImageEmbeddingConfigResponse,
    ImageEmbeddingConfigUpsert,
    SimilarityCandidateResponse,
    SimilarityClusterResponse,
    SimilarityDecisionRequest,
    SimilarityProductResponse,
    SimilaritySourceResponse,
    SimilarityTaskStatsResponse,
)
from .services import product_to_item
from .similarity_services import (
    decrypt_api_key,
    encrypt_api_key,
    enqueue_all_products,
    mask_api_key,
    merge_products,
    product_sources,
    recalculate_cluster_representative,
    task_stats,
)

router = APIRouter(prefix="/api/similarity", tags=["similarity"])
settings = get_settings()


def config_response(config: ImageEmbeddingConfig) -> ImageEmbeddingConfigResponse:
    key = decrypt_api_key(config.api_key_encrypted, settings)
    return ImageEmbeddingConfigResponse(
        id=config.id,
        name=config.name,
        base_url=config.base_url,
        api_key_masked=mask_api_key(key),
        model_name=config.model_name,
        output_dimension=config.output_dimension,
        request_template=config.request_template,
        response_path=config.response_path,
        timeout_seconds=config.timeout_seconds,
        top_k=config.top_k,
        phash_auto_distance=config.phash_auto_distance,
        review_threshold=config.review_threshold,
        auto_merge_threshold=config.auto_merge_threshold,
        auto_merge_enabled=config.auto_merge_enabled,
        is_active=config.is_active,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def similarity_product(db: Session, product: Product) -> SimilarityProductResponse:
    base = product_to_item(product).model_dump()
    return SimilarityProductResponse(
        **base,
        similarity_status=product.similarity_status,
        sources=[SimilaritySourceResponse(**source) for source in product_sources(db, product.id)],
    )


@router.get("/admin/configs", response_model=list[ImageEmbeddingConfigResponse])
def list_configs(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ImageEmbeddingConfigResponse]:
    configs = db.scalars(select(ImageEmbeddingConfig).order_by(ImageEmbeddingConfig.id.desc())).all()
    return [config_response(config) for config in configs]


@router.post("/admin/configs", response_model=ImageEmbeddingConfigResponse, status_code=status.HTTP_201_CREATED)
def create_config(
    payload: ImageEmbeddingConfigUpsert,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ImageEmbeddingConfigResponse:
    if payload.activate:
        db.execute(update(ImageEmbeddingConfig).values(is_active=False))
    config = ImageEmbeddingConfig(
        name=payload.name,
        base_url=str(payload.base_url),
        api_key_encrypted=encrypt_api_key(payload.api_key, settings),
        model_name=payload.model_name,
        output_dimension=payload.output_dimension,
        request_template=payload.request_template,
        response_path=payload.response_path,
        timeout_seconds=payload.timeout_seconds,
        top_k=payload.top_k,
        phash_auto_distance=payload.phash_auto_distance,
        review_threshold=payload.review_threshold,
        auto_merge_threshold=payload.auto_merge_threshold,
        auto_merge_enabled=payload.auto_merge_enabled,
        is_active=payload.activate,
        created_by=admin.id,
    )
    db.add(config)
    db.flush()
    if config.is_active:
        enqueue_all_products(db, config)
    db.commit()
    db.refresh(config)
    return config_response(config)


@router.put("/admin/configs/{config_id}", response_model=ImageEmbeddingConfigResponse)
def update_config(
    config_id: int,
    payload: ImageEmbeddingConfigUpsert,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ImageEmbeddingConfigResponse:
    current = db.get(ImageEmbeddingConfig, config_id)
    if not current:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    changed_model = (
        current.base_url != str(payload.base_url)
        or current.model_name != payload.model_name
        or current.output_dimension != payload.output_dimension
        or current.request_template != payload.request_template
        or current.response_path != payload.response_path
        or payload.api_key is not None
    )
    if changed_model:
        return create_config(payload, admin, db)
    current.name = payload.name
    current.timeout_seconds = payload.timeout_seconds
    current.top_k = payload.top_k
    current.phash_auto_distance = payload.phash_auto_distance
    current.review_threshold = payload.review_threshold
    current.auto_merge_threshold = payload.auto_merge_threshold
    current.auto_merge_enabled = payload.auto_merge_enabled
    if payload.activate:
        db.execute(update(ImageEmbeddingConfig).where(ImageEmbeddingConfig.id != current.id).values(is_active=False))
        current.is_active = True
    db.commit()
    db.refresh(current)
    return config_response(current)


@router.post("/admin/configs/{config_id}/rebuild", response_model=SimilarityTaskStatsResponse)
def rebuild_config(
    config_id: int,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    config = db.get(ImageEmbeddingConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    db.execute(
        update(SimilarityTask)
        .where(SimilarityTask.config_id == config.id, SimilarityTask.status == "failed")
        .values(status="pending", attempts=0, last_error=None, available_at=datetime.now(timezone.utc))
    )
    enqueue_all_products(db, config, force=True)
    db.commit()
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.get("/admin/stats", response_model=SimilarityTaskStatsResponse)
def stats(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.get("/admin/candidates", response_model=list[SimilarityCandidateResponse])
def candidates(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    candidate_status: str = Query(default="pending", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[SimilarityCandidateResponse]:
    rows = db.scalars(
        select(ProductSimilarityCandidate)
        .where(ProductSimilarityCandidate.status == candidate_status)
        .order_by(ProductSimilarityCandidate.image_similarity.desc().nullslast(), ProductSimilarityCandidate.id)
        .limit(limit)
    ).all()
    result = []
    for candidate in rows:
        left = db.get(Product, candidate.left_product_id)
        right = db.get(Product, candidate.right_product_id)
        if left and right:
            result.append(SimilarityCandidateResponse(
                id=candidate.id,
                left=similarity_product(db, left),
                right=similarity_product(db, right),
                phash_distance=candidate.phash_distance,
                image_similarity=candidate.image_similarity,
                status=candidate.status,
                created_at=candidate.created_at,
            ))
    return result


@router.post("/admin/candidates/{candidate_id}/decision", response_model=SimilarityCandidateResponse)
def decide_candidate(
    candidate_id: int,
    payload: SimilarityDecisionRequest,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityCandidateResponse:
    candidate = db.get(ProductSimilarityCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选记录不存在")
    now = datetime.now(timezone.utc)
    candidate.reviewed_by = admin.id
    candidate.reviewed_at = now
    candidate.decision_source = "manual"
    if payload.decision == "approve":
        candidate.status = "approved"
        merge_products(
            db,
            candidate.left_product_id,
            candidate.right_product_id,
            "manual",
            candidate.image_similarity,
            actor_id=admin.id,
            candidate_id=candidate.id,
        )
    else:
        candidate.status = "rejected"
        db.add(SimilarityAuditLog(
            action="reject",
            actor_id=admin.id,
            candidate_id=candidate.id,
            payload={"left_product_id": candidate.left_product_id, "right_product_id": candidate.right_product_id},
        ))
    db.commit()
    left = db.get(Product, candidate.left_product_id)
    right = db.get(Product, candidate.right_product_id)
    return SimilarityCandidateResponse(
        id=candidate.id,
        left=similarity_product(db, left),
        right=similarity_product(db, right),
        phash_distance=candidate.phash_distance,
        image_similarity=candidate.image_similarity,
        status=candidate.status,
        created_at=candidate.created_at,
    )


@router.get("/clusters/{cluster_id}", response_model=SimilarityClusterResponse)
def cluster_detail(
    cluster_id: int,
    _: Annotated[AdminUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityClusterResponse:
    cluster = db.get(ProductSimilarityCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="同款簇不存在")
    members = db.scalars(
        select(Product)
        .join(ProductSimilarityClusterMember, ProductSimilarityClusterMember.product_id == Product.id)
        .where(ProductSimilarityClusterMember.cluster_id == cluster.id)
        .order_by(Product.total_pieces.desc(), Product.daily_orders.desc(), Product.last_collected_at.desc())
    ).all()
    representative = db.get(Product, cluster.representative_product_id)
    return SimilarityClusterResponse(
        id=cluster.id,
        representative=similarity_product(db, representative),
        members=[similarity_product(db, product) for product in members],
    )


@router.delete("/admin/clusters/{cluster_id}/members/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_cluster_member(
    cluster_id: int,
    product_id: int,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    member = db.scalar(select(ProductSimilarityClusterMember).where(
        ProductSimilarityClusterMember.cluster_id == cluster_id,
        ProductSimilarityClusterMember.product_id == product_id,
    ))
    if not member:
        raise HTTPException(status_code=404, detail="簇成员不存在")
    db.delete(member)
    db.flush()
    recalculate_cluster_representative(db, cluster_id)
    db.add(SimilarityAuditLog(
        action="remove_member",
        actor_id=admin.id,
        cluster_id=cluster_id,
        payload={"product_id": product_id},
    ))
    db.commit()
