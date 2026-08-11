from __future__ import annotations

from copy import copy
from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .auth import get_current_user, require_admin
from .config import Settings, get_settings
from .database import get_db
from .models import (
    AdminUser,
    CrossPlatformProductRelation,
    CrossPlatformSimilarityCandidate,
    ImageEmbeddingConfig,
    Product,
    ProductGroup,
    ProductGroupMembership,
    ProductImageEmbedding,
    ProductSimilarityCandidate,
    ProductSimilarityCluster,
    ProductSimilarityClusterMember,
    SimilarityAuditLog,
    SimilarityScopePreference,
    SimilarityTask,
    TemuGroupMembership,
    TemuImageEmbedding,
    TemuProduct,
    TemuProductGroup,
    TemuSimilarityCandidate,
    TemuSimilarityClusterMember,
    TemuSimilarityTask,
)
from .schemas import (
    ImageEmbeddingConfigResponse,
    ImageEmbeddingConfigUpsert,
    SimilarityBatchDecisionRequest,
    SimilarityBatchDecisionResponse,
    SimilarityCandidateProductResponse,
    SimilarityCandidateResponse,
    SimilarityClusterResponse,
    SimilarityConnectionTestRequest,
    SimilarityConnectionTestResponse,
    SimilarityDecisionRequest,
    SimilarityProductResponse,
    SimilarityQueueUpdate,
    SimilarityScopeGroupResponse,
    SimilarityScopePreferenceResponse,
    SimilarityScopeRequest,
    SimilaritySourceResponse,
    SimilarityTaskResponse,
    SimilarityTaskStatsResponse,
)
from .services import product_to_item
from .temu_services import temu_product_to_item
from .similarity_services import (
    decrypt_api_key,
    embedding_endpoint_url,
    encrypt_api_key,
    gemini_request_template,
    image_fingerprints,
    mask_api_key,
    merge_products,
    merge_temu_products,
    product_sources,
    queue_control,
    enqueue_scope_products,
    enqueue_scope_temu_products,
    recalculate_cluster_representative,
    request_embedding,
    reset_embedding_scope,
    reset_temu_embedding_scope,
    scope_product_ids,
    scope_temu_product_ids,
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
        request_template=gemini_request_template(),
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
        request_template=gemini_request_template(),
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


def resolve_scope_ids(db: Session, payload: SimilarityScopeRequest) -> tuple[list[int], list[int]]:
    return (
        scope_product_ids(db, payload.owner_id, payload.group_ids),
        scope_temu_product_ids(db, payload.owner_id, payload.temu_group_ids),
    )


@router.post("/admin/configs/{config_id}/enqueue", response_model=SimilarityTaskStatsResponse)
def enqueue_config_scope(
    config_id: int,
    payload: SimilarityScopeRequest,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    config = db.get(ImageEmbeddingConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        product_ids, temu_product_ids = resolve_scope_ids(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    enqueue_scope_products(db, config, product_ids)
    enqueue_scope_temu_products(db, config, temu_product_ids)
    queue_control(db).state = "running"
    db.commit()
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.post("/admin/configs/{config_id}/rebuild", response_model=SimilarityTaskStatsResponse)
def rebuild_config(
    config_id: int,
    payload: SimilarityScopeRequest,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    config = db.get(ImageEmbeddingConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        product_ids, temu_product_ids = resolve_scope_ids(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    reset_embedding_scope(db, config, product_ids)
    reset_temu_embedding_scope(db, config, temu_product_ids)
    queue_control(db).state = "running"
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


@router.get("/admin/scopes", response_model=list[SimilarityScopeGroupResponse])
def list_scopes(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    owner_id: int = Query(gt=0),
    config_id: int | None = Query(default=None, gt=0),
) -> list[SimilarityScopeGroupResponse]:
    config = db.get(ImageEmbeddingConfig, config_id) if config_id else db.scalar(
        select(ImageEmbeddingConfig).where(ImageEmbeddingConfig.is_active.is_(True))
    )
    result: list[SimilarityScopeGroupResponse] = []
    product_rows = db.execute(select(
        ProductGroup.id, ProductGroup.owner_id, ProductGroup.name,
        func.count(func.distinct(ProductGroupMembership.product_id)),
        func.count(func.distinct(ProductGroupMembership.product_id)).filter(
            Product.cached_image_name.is_not(None), Product.image_status == "ready"
        ),
    ).outerjoin(ProductGroupMembership, ProductGroupMembership.group_id == ProductGroup.id)
      .outerjoin(Product, Product.id == ProductGroupMembership.product_id)
      .where(ProductGroup.owner_id == owner_id)
      .group_by(ProductGroup.id, ProductGroup.owner_id, ProductGroup.name)
      .order_by(ProductGroup.name.asc())).all()
    for group_id, row_owner_id, name, product_count, ready_count in product_rows:
        processed = int(db.scalar(select(func.count(func.distinct(ProductImageEmbedding.product_id))).join(
            ProductGroupMembership, ProductGroupMembership.product_id == ProductImageEmbedding.product_id
        ).where(
            ProductGroupMembership.group_id == group_id,
            ProductImageEmbedding.config_id == config.id if config else False,
        )) or 0)
        result.append(SimilarityScopeGroupResponse(
            id=group_id, owner_id=row_owner_id, platform="1688", name=name,
            product_count=int(product_count), ready_product_count=int(ready_count),
            processed_product_count=processed, pending_product_count=max(0, int(ready_count) - processed),
        ))
    temu_rows = db.execute(select(
        TemuProductGroup.id, TemuProductGroup.owner_id, TemuProductGroup.name,
        func.count(func.distinct(TemuGroupMembership.product_id)),
        func.count(func.distinct(TemuGroupMembership.product_id)).filter(
            TemuProduct.cached_image_name.is_not(None), TemuProduct.image_status == "ready"
        ),
    ).outerjoin(TemuGroupMembership, TemuGroupMembership.group_id == TemuProductGroup.id)
      .outerjoin(TemuProduct, TemuProduct.id == TemuGroupMembership.product_id)
      .where(TemuProductGroup.owner_id == owner_id)
      .group_by(TemuProductGroup.id, TemuProductGroup.owner_id, TemuProductGroup.name)
      .order_by(TemuProductGroup.name.asc())).all()
    for group_id, row_owner_id, name, product_count, ready_count in temu_rows:
        processed = int(db.scalar(select(func.count(func.distinct(TemuImageEmbedding.product_id))).join(
            TemuGroupMembership, TemuGroupMembership.product_id == TemuImageEmbedding.product_id
        ).where(
            TemuGroupMembership.group_id == group_id,
            TemuImageEmbedding.config_id == config.id if config else False,
        )) or 0)
        result.append(SimilarityScopeGroupResponse(
            id=group_id, owner_id=row_owner_id, platform="temu", name=name,
            product_count=int(product_count), ready_product_count=int(ready_count),
            processed_product_count=processed, pending_product_count=max(0, int(ready_count) - processed),
        ))
    return result


@router.get("/admin/scope-preference", response_model=SimilarityScopePreferenceResponse)
def get_scope_preference(
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityScopePreferenceResponse:
    preference = db.get(SimilarityScopePreference, admin.id)
    return SimilarityScopePreferenceResponse(
        owner_id=preference.owner_id if preference else None,
        group_ids=list(preference.product_group_ids or []) if preference else [],
        temu_group_ids=list(preference.temu_group_ids or []) if preference else [],
    )


@router.put("/admin/scope-preference", response_model=SimilarityScopePreferenceResponse)
def save_scope_preference(
    payload: SimilarityScopeRequest,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityScopePreferenceResponse:
    try:
        scope_product_ids(db, payload.owner_id, payload.group_ids)
        scope_temu_product_ids(db, payload.owner_id, payload.temu_group_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    preference = db.get(SimilarityScopePreference, admin.id) or SimilarityScopePreference(admin_id=admin.id)
    preference.owner_id = payload.owner_id
    preference.product_group_ids = payload.group_ids
    preference.temu_group_ids = payload.temu_group_ids
    db.add(preference)
    db.commit()
    return SimilarityScopePreferenceResponse(
        owner_id=preference.owner_id,
        group_ids=list(preference.product_group_ids or []),
        temu_group_ids=list(preference.temu_group_ids or []),
    )


@router.put("/admin/queue", response_model=SimilarityTaskStatsResponse)
def update_queue(
    payload: SimilarityQueueUpdate,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    control = queue_control(db)
    control.concurrency = payload.concurrency
    control.auto_vectorization_enabled = payload.auto_vectorization_enabled
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


@router.delete("/admin/tasks/failed", response_model=SimilarityTaskStatsResponse)
def clear_failed_tasks(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityTaskStatsResponse:
    db.execute(delete(SimilarityTask).where(SimilarityTask.status == "failed"))
    db.commit()
    return SimilarityTaskStatsResponse(**task_stats(db))


@router.post("/admin/configs/{config_id}/test", response_model=SimilarityConnectionTestResponse)
async def test_config(
    config_id: int,
    payload: SimilarityConnectionTestRequest,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityConnectionTestResponse:
    saved_config = db.get(ImageEmbeddingConfig, config_id)
    if not saved_config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    config = copy(saved_config)
    config.base_url = str(payload.base_url)
    config.model_name = payload.model_name
    config.output_dimension = payload.output_dimension
    config.response_path = payload.response_path
    config.timeout_seconds = payload.timeout_seconds
    if payload.api_key is not None:
        config.api_key_encrypted = encrypt_api_key(payload.api_key, settings)
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
        endpoint=embedding_endpoint_url(config.base_url, config.model_name),
        returned_dimension=config.output_dimension,
        elapsed_ms=elapsed_ms,
        message=f"接口正常，返回 {config.output_dimension} 维向量（存储长度 {len(vector)}）",
    )


def candidate_product(db: Session, product: Product | TemuProduct) -> SimilarityCandidateProductResponse:
    if isinstance(product, Product):
        return SimilarityCandidateProductResponse(
            platform="1688", id=product.id, external_id=product.offer_id, product_url=product.product_url,
            image_url=f"/images/{product.cached_image_name}" if product.cached_image_name else None,
            metric_label="总件数", metric_value=product.total_pieces,
            sources=[SimilaritySourceResponse(**source) for source in product_sources(db, product.id)],
        )
    return SimilarityCandidateProductResponse(
        platform="temu", id=product.id, external_id=product.temu_product_id, title=product.title,
        product_url=product.product_url,
        image_url=f"/images/{product.cached_image_name}" if product.cached_image_name else product.image_url,
        metric_label="总销量", metric_value=product.total_sales,
    )


def candidate_response(db: Session, candidate_type: str, candidate) -> SimilarityCandidateResponse | None:
    if candidate_type == "1688_same":
        left, right = db.get(Product, candidate.left_product_id), db.get(Product, candidate.right_product_id)
    elif candidate_type == "temu_same":
        left, right = db.get(TemuProduct, candidate.left_product_id), db.get(TemuProduct, candidate.right_product_id)
    else:
        left, right = db.get(Product, candidate.product_id), db.get(TemuProduct, candidate.temu_product_id)
    if not left or not right:
        return None
    return SimilarityCandidateResponse(
        id=candidate.id, candidate_type=candidate_type,
        left=candidate_product(db, left), right=candidate_product(db, right),
        phash_distance=candidate.phash_distance, image_similarity=candidate.image_similarity,
        status=candidate.status, created_at=candidate.created_at,
    )


@router.get("/admin/candidates", response_model=list[SimilarityCandidateResponse])
def candidates(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    candidate_status: str = Query(default="pending", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[SimilarityCandidateResponse]:
    combined = []
    for candidate_type, model in (
        ("1688_same", ProductSimilarityCandidate),
        ("temu_same", TemuSimilarityCandidate),
        ("cross_platform", CrossPlatformSimilarityCandidate),
    ):
        for candidate in db.scalars(select(model).where(model.status == candidate_status).order_by(
            model.image_similarity.desc().nullslast(), model.id
        ).limit(limit)).all():
            response = candidate_response(db, candidate_type, candidate)
            if response:
                combined.append(response)
    combined.sort(key=lambda item: item.image_similarity or -2, reverse=True)
    return combined[:limit]


def apply_candidate_decision(
    db: Session,
    candidate: ProductSimilarityCandidate,
    decision: Literal["approve", "reject"],
    admin_id: int,
) -> None:
    candidate.reviewed_by = admin_id
    candidate.reviewed_at = datetime.now(timezone.utc)
    candidate.decision_source = "manual"
    if decision == "approve":
        candidate.status = "approved"
        merge_products(
            db,
            candidate.left_product_id,
            candidate.right_product_id,
            "manual",
            candidate.image_similarity,
            actor_id=admin_id,
            candidate_id=candidate.id,
        )
        return
    candidate.status = "rejected"
    db.add(SimilarityAuditLog(
        action="reject",
        actor_id=admin_id,
        candidate_id=candidate.id,
        payload={"left_product_id": candidate.left_product_id, "right_product_id": candidate.right_product_id},
    ))


def apply_typed_candidate_decision(db: Session, candidate_type: str, candidate_id: int, decision: str, admin_id: int) -> None:
    model = {
        "1688_same": ProductSimilarityCandidate,
        "temu_same": TemuSimilarityCandidate,
        "cross_platform": CrossPlatformSimilarityCandidate,
    }[candidate_type]
    candidate = db.get(model, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选记录不存在")
    if candidate.status != "pending":
        raise HTTPException(status_code=409, detail="候选已审批")
    candidate.reviewed_by = admin_id
    candidate.reviewed_at = datetime.now(timezone.utc)
    candidate.decision_source = "manual"
    candidate.status = "approved" if decision == "approve" else "rejected"
    if decision == "approve" and candidate_type == "1688_same":
        merge_products(db, candidate.left_product_id, candidate.right_product_id, "manual", candidate.image_similarity,
                       actor_id=admin_id, candidate_id=candidate.id)
    elif decision == "approve" and candidate_type == "temu_same":
        merge_temu_products(db, candidate.left_product_id, candidate.right_product_id, candidate.image_similarity,
                            admin_id, candidate.id)
    elif decision == "approve":
        relation = db.scalar(select(CrossPlatformProductRelation).where(
            CrossPlatformProductRelation.product_id == candidate.product_id,
            CrossPlatformProductRelation.temu_product_id == candidate.temu_product_id,
        )) or CrossPlatformProductRelation(
            product_id=candidate.product_id, temu_product_id=candidate.temu_product_id
        )
        relation.confidence = candidate.image_similarity
        relation.confirmed_by = admin_id
        relation.candidate_id = candidate.id
        db.add(relation)
        db.add(SimilarityAuditLog(
            action="link", actor_id=admin_id, candidate_type="cross_platform", external_candidate_id=candidate.id,
            payload={"product_id": candidate.product_id, "temu_product_id": candidate.temu_product_id},
        ))
    elif candidate_type != "1688_same":
        db.add(SimilarityAuditLog(
            action="reject", actor_id=admin_id, candidate_type=candidate_type, external_candidate_id=candidate.id,
        ))
    else:
        db.add(SimilarityAuditLog(
            action="reject", actor_id=admin_id, candidate_id=candidate.id, candidate_type="1688_same",
        ))


@router.post("/admin/candidates/batch-decision", response_model=SimilarityBatchDecisionResponse)
def decide_candidates_batch(
    payload: SimilarityBatchDecisionRequest,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityBatchDecisionResponse:
    try:
        for typed_id in payload.candidate_ids:
            candidate_type, candidate_id = typed_id.split(":", 1)
            apply_typed_candidate_decision(db, candidate_type, int(candidate_id), payload.decision, admin.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return SimilarityBatchDecisionResponse(decision=payload.decision, processed=len(payload.candidate_ids))


@router.post("/admin/candidates/{candidate_type}/{candidate_id}/decision", response_model=SimilarityCandidateResponse)
def decide_candidate(
    candidate_type: Literal["1688_same", "temu_same", "cross_platform"],
    candidate_id: int,
    payload: SimilarityDecisionRequest,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SimilarityCandidateResponse:
    apply_typed_candidate_decision(db, candidate_type, candidate_id, payload.decision, admin.id)
    model = {
        "1688_same": ProductSimilarityCandidate,
        "temu_same": TemuSimilarityCandidate,
        "cross_platform": CrossPlatformSimilarityCandidate,
    }[candidate_type]
    candidate = db.get(model, candidate_id)
    db.commit()
    return candidate_response(db, candidate_type, candidate)


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
