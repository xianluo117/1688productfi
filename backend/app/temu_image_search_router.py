from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_admin
from .config import get_settings
from .database import get_db
from .models import (
    AdminUser,
    TemuImageSearchTask,
    TemuProduct,
    TemuProductGroup,
)
from .schemas import (
    TemuGroupResponse,
    TemuImageSearchConfigResponse,
    TemuImageSearchConfigUpsert,
    TemuImageSearchHealthResponse,
    TemuImageSearchScopeRequest,
    TemuImageSearchStatsResponse,
    TemuImageSearchTaskResponse,
)
from .temu_image_search_services import (
    config_payload,
    configured_search_api_key,
    enqueue_temu_image_search_scope,
    image_search_config,
    image_search_stats,
    reset_image_search_task,
    update_image_search_config,
)

router = APIRouter(prefix="/api/temu-image-search/admin", tags=["temu-image-search"])
settings = get_settings()


def task_response(
    task: TemuImageSearchTask,
    product: TemuProduct,
    owner: AdminUser | None,
    group: TemuProductGroup | None,
) -> TemuImageSearchTaskResponse:
    return TemuImageSearchTaskResponse(
        id=task.id,
        product_id=product.id,
        temu_product_id=product.temu_product_id,
        title=product.title,
        owner_id=task.owner_id,
        owner_name=owner.username if owner else None,
        group_id=task.group_id,
        group_name=group.name if group else None,
        image_url=task.image_url,
        source=task.source,
        status=task.status,
        remote_task_id=task.remote_task_id,
        attempts=task.attempts,
        last_error_code=task.last_error_code,
        last_error=task.last_error,
        available_at=task.available_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
    )


def task_row(db: Session, task_id: int):
    return db.execute(
        select(TemuImageSearchTask, TemuProduct, AdminUser, TemuProductGroup)
        .join(TemuProduct, TemuProduct.id == TemuImageSearchTask.product_id)
        .outerjoin(AdminUser, AdminUser.id == TemuImageSearchTask.owner_id)
        .outerjoin(TemuProductGroup, TemuProductGroup.id == TemuImageSearchTask.group_id)
        .where(TemuImageSearchTask.id == task_id)
    ).one()


@router.get("/config", response_model=TemuImageSearchConfigResponse)
def get_config(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageSearchConfigResponse:
    config = image_search_config(db)
    return TemuImageSearchConfigResponse(**config_payload(config, settings))


@router.put("/config", response_model=TemuImageSearchConfigResponse)
def save_config(
    payload: TemuImageSearchConfigUpsert,
    admin: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageSearchConfigResponse:
    config = image_search_config(db)
    update_image_search_config(
        db,
        config,
        base_url=str(payload.base_url),
        api_key=payload.api_key,
        auto_search_enabled=payload.auto_search_enabled,
        timeout_seconds=payload.timeout_seconds,
        poll_interval_seconds=payload.poll_interval_seconds,
        task_timeout_seconds=payload.task_timeout_seconds,
        updated_by=admin.id,
        settings=settings,
    )
    return TemuImageSearchConfigResponse(**config_payload(config, settings))


@router.get("/health", response_model=TemuImageSearchHealthResponse)
async def health(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageSearchHealthResponse:
    config = image_search_config(db)
    try:
        api_key = configured_search_api_key(config, settings)
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.get(
                f"{config.base_url.rstrip('/')}/api/v1/health",
                headers={"X-API-Key": api_key},
            )
        response.raise_for_status()
        payload = response.json()
        tasks = payload.get("tasks") or {}
        cookie = payload.get("cookie") or {}
        return TemuImageSearchHealthResponse(
            status=str(payload.get("status") or "unknown"),
            worker_limit=payload.get("worker_limit"),
            cookie_available=cookie.get("available"),
            queued=int(tasks.get("queued") or 0),
            running=int(tasks.get("running") or 0),
            succeeded=int(tasks.get("succeeded") or 0),
            failed=int(tasks.get("failed") or 0),
        )
    except Exception as error:
        return TemuImageSearchHealthResponse(status="error", message=str(error)[:1000])


@router.get("/groups", response_model=list[TemuGroupResponse])
def groups(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    owner_id: int = Query(gt=0),
) -> list[TemuProductGroup]:
    return list(
        db.scalars(
            select(TemuProductGroup)
            .where(TemuProductGroup.owner_id == owner_id)
            .order_by(TemuProductGroup.name.asc())
        )
    )


@router.post("/stats", response_model=TemuImageSearchStatsResponse)
def stats(
    payload: TemuImageSearchScopeRequest,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageSearchStatsResponse:
    try:
        result = image_search_stats(db, payload.owner_id, payload.group_ids)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return TemuImageSearchStatsResponse(**result)


@router.post("/enqueue", response_model=TemuImageSearchStatsResponse)
def enqueue_scope(
    payload: TemuImageSearchScopeRequest,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageSearchStatsResponse:
    config = image_search_config(db)
    try:
        configured_search_api_key(config, settings)
        enqueue_temu_image_search_scope(db, payload.owner_id, payload.group_ids)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    return TemuImageSearchStatsResponse(
        **image_search_stats(db, payload.owner_id, payload.group_ids)
    )


@router.get("/tasks", response_model=list[TemuImageSearchTaskResponse])
def tasks(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    owner_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[TemuImageSearchTaskResponse]:
    statement = (
        select(TemuImageSearchTask, TemuProduct, AdminUser, TemuProductGroup)
        .join(TemuProduct, TemuProduct.id == TemuImageSearchTask.product_id)
        .outerjoin(AdminUser, AdminUser.id == TemuImageSearchTask.owner_id)
        .outerjoin(TemuProductGroup, TemuProductGroup.id == TemuImageSearchTask.group_id)
        .order_by(TemuImageSearchTask.id.desc())
        .limit(limit)
    )
    if status:
        statement = statement.where(TemuImageSearchTask.status == status)
    if source:
        statement = statement.where(TemuImageSearchTask.source == source)
    if owner_id:
        statement = statement.where(TemuImageSearchTask.owner_id == owner_id)
    return [task_response(*row) for row in db.execute(statement).all()]


@router.post("/tasks/{task_id}/retry", response_model=TemuImageSearchTaskResponse)
def retry_task(
    task_id: int,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageSearchTaskResponse:
    task = db.get(TemuImageSearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="图搜任务不存在")
    reset_image_search_task(task, source="manual")
    db.commit()
    return task_response(*task_row(db, task_id))


@router.post("/tasks/retry-failed", response_model=dict[str, int])
def retry_failed(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    owner_id: int | None = Query(default=None, gt=0),
) -> dict[str, int]:
    statement = select(TemuImageSearchTask).where(TemuImageSearchTask.status == "failed")
    if owner_id:
        statement = statement.where(TemuImageSearchTask.owner_id == owner_id)
    failed_tasks = list(db.scalars(statement))
    for task in failed_tasks:
        reset_image_search_task(task, source="manual")
    db.commit()
    return {"retried": len(failed_tasks)}
