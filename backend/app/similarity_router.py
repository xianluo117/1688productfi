from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
    SimilarityConnectionTestResponse,
    SimilarityDecisionRequest,
    SimilarityProductResponse,
    SimilarityQueueUpdate,
    SimilaritySourceResponse,
    SimilarityTaskResponse,
    SimilarityTaskStatsResponse,
)
from .services import product_to_item
from .similarity_services import (
    decrypt_api_key,
    embedding_endpoint_url,
    encrypt_api_key,
    enqueue_all_products,
    image_fingerprints,
    mask_api_key,
    merge_products,
    product_sources,
    queue_control,
    recalculate_cluster_representative,
    request_embedding,
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


def task_response(task: SimilarityTask, product: Product, config: ImageEmbeddingConfig) -> SimilarityTaskResponse:
    return SimilarityTaskResponse(
        id=task.id,
        product_id=product.id,
        offer_id=product.offer_id,
        config_id=config.id,
        config_name=config.name,
        status=task.status,
        attempts=task.attempts,
        last_error=task.last_error,
        available_at=task.available_at,
        locked_at=task.locked_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
    )


@router.get("/admin/stats", response_model=SimilarityTaskStatsResponse)
def stats(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.put("/admin/queue", response_model=SimilarityTaskStatsResponse)
def update_queue(
    payload: SimilarityQueueUpdate,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    control = queue_control(db)
    control.concurrency = payload.concurrency
    db.commit()
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.post("/admin/queue/{action}", response_model=SimilarityTaskStatsResponse)
def control_queue(
    action: Literal["pause", "resume", "stop"],
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    control = queue_control(db)
    if action == "pause":
        control.state = "paused"
    elif action == "resume":
        control.state = "running"
    else:
        control.state = "stopped"
        db.execute(
            update(SimilarityTask)
            .where(SimilarityTask.status == "pending")
            .values(status="cancelled", last_error="管理员停止并清空等待队列", finished_at=datetime.now(timezone.utc))
        )
    db.commit()
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.get("/admin/tasks", response_model=list[SimilarityTaskResponse])
def list_tasks(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    task_status: str = Query(default="failed", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[SimilarityTaskResponse]:
    rows = db.execute(
        select(SimilarityTask, Product, ImageEmbeddingConfig)
        .join(Product, Product.id == SimilarityTask.product_id)
        .join(ImageEmbeddingConfig, ImageEmbeddingConfig.id == SimilarityTask.config_id)
        .where(SimilarityTask.status == task_status)
        .order_by(SimilarityTask.id.desc())
        .limit(limit)
    ).all()
    return [task_response(task, product, config) for task, product, config in rows]


@router.post("/admin/tasks/id/{task_id}/retry", response_model=SimilarityTaskStatsResponse)
def retry_task(
    task_id: int,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    task = db.get(SimilarityTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.status = "pending"
    task.attempts = 0
    task.last_error = None
    task.available_at = datetime.now(timezone.utc)
    task.locked_at = None
    task.finished_at = None
    queue_control(db).state = "running"
    db.commit()
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.post("/admin/tasks/retry-failed", response_model=SimilarityTaskStatsResponse)
def retry_failed_tasks(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    db.execute(
        update(SimilarityTask)
        .where(SimilarityTask.status == "failed")
        .values(status="pending", attempts=0, last_error=None, available_at=datetime.now(timezone.utc), locked_at=None, finished_at=None)
    )
    queue_control(db).state = "running"
    db.commit()
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.post("/admin/configs/{config_id}/test", response_model=SimilarityConnectionTestResponse)
async def test_config(
    config_id: int,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityConnectionTestResponse:
    config = db.get(ImageEmbeddingConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    product = db.scalar(
        select(Product)
        .where(Product.cached_image_name.is_not(None), Product.image_status == "ready")
        .order_by(Product.last_collected_at.desc())
    )
    if not product or not product.cached_image_name:
        raise HTTPException(status_code=400, detail="没有可用于测试的已缓存商品图片")
    path = settings.image_storage_path / product.cached_image_name
    try:
        _, _, content = image_fingerprints(path)
        started = perf_counter()
        vector = await request_embedding(config, product, content, settings)
        elapsed_ms = int((perf_counter() - started) * 1000)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return SimilarityConnectionTestResponse(
        success=True,
        product_id=product.id,
        offer_id=product.offer_id,
        endpoint=embedding_endpoint_url(config.base_url),
        returned_dimension=config.output_dimension,
        elapsed_ms=elapsed_ms,
        message=f"接口正常，返回 {config.output_dimension} 维向量（存储长度 {len(vector)}）",
    )


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


@router.delete(
    "/admin/clusters/{cluster_id}/members/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_cluster_member(
    cluster_id: int,
    product_id: int,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)
