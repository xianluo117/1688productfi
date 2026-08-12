from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_admin
from .config import get_settings
from .database import get_db
from .models import (
    AdminUser,
    TemuImageDownloadTask,
    TemuProduct,
    TemuProductGroup,
)
from .schemas import (
    TemuGroupResponse,
    TemuImageDownloadScopeRequest,
    TemuImageDownloadStatsResponse,
    TemuImageDownloadTaskResponse,
)
from .temu_image_download_services import (
    enqueue_temu_image_scope,
    temu_image_download_stats,
)

router = APIRouter(prefix="/api/temu-image-downloads/admin", tags=["temu-image-downloads"])
settings = get_settings()


def task_response(
    task: TemuImageDownloadTask,
    product: TemuProduct,
    owner: AdminUser | None,
    group: TemuProductGroup | None,
) -> TemuImageDownloadTaskResponse:
    return TemuImageDownloadTaskResponse(
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
        attempts=task.attempts,
        last_error=task.last_error,
        available_at=task.available_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
    )


@router.get("/groups", response_model=list[TemuGroupResponse])
def groups(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    owner_id: int = Query(gt=0),
) -> list[TemuProductGroup]:
    return list(db.scalars(
        select(TemuProductGroup)
        .where(TemuProductGroup.owner_id == owner_id)
        .order_by(TemuProductGroup.name.asc())
    ))


@router.post("/stats", response_model=TemuImageDownloadStatsResponse)
def stats(
    payload: TemuImageDownloadScopeRequest,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageDownloadStatsResponse:
    try:
        result = temu_image_download_stats(
            db,
            payload.owner_id,
            payload.group_ids,
            settings.image_storage_path,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return TemuImageDownloadStatsResponse(**result)


@router.post("/enqueue", response_model=TemuImageDownloadStatsResponse)
def enqueue_scope(
    payload: TemuImageDownloadScopeRequest,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageDownloadStatsResponse:
    try:
        enqueue_temu_image_scope(
            db,
            payload.owner_id,
            payload.group_ids,
            settings.image_storage_path,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    return TemuImageDownloadStatsResponse(**temu_image_download_stats(
        db,
        payload.owner_id,
        payload.group_ids,
        settings.image_storage_path,
    ))


@router.get("/tasks", response_model=list[TemuImageDownloadTaskResponse])
def tasks(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    owner_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[TemuImageDownloadTaskResponse]:
    statement = (
        select(TemuImageDownloadTask, TemuProduct, AdminUser, TemuProductGroup)
        .join(TemuProduct, TemuProduct.id == TemuImageDownloadTask.product_id)
        .outerjoin(AdminUser, AdminUser.id == TemuImageDownloadTask.owner_id)
        .outerjoin(TemuProductGroup, TemuProductGroup.id == TemuImageDownloadTask.group_id)
        .order_by(TemuImageDownloadTask.id.desc())
        .limit(limit)
    )
    if status:
        statement = statement.where(TemuImageDownloadTask.status == status)
    if source:
        statement = statement.where(TemuImageDownloadTask.source == source)
    if owner_id:
        statement = statement.where(TemuImageDownloadTask.owner_id == owner_id)
    return [task_response(*row) for row in db.execute(statement).all()]


def reset_task(task: TemuImageDownloadTask) -> None:
    task.status = "pending"
    task.attempts = 0
    task.last_error = None
    task.available_at = datetime.now(timezone.utc)
    task.locked_at = None
    task.started_at = None
    task.finished_at = None


@router.post("/tasks/{task_id}/retry", response_model=TemuImageDownloadTaskResponse)
def retry_task(
    task_id: int,
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TemuImageDownloadTaskResponse:
    task = db.get(TemuImageDownloadTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="下载任务不存在")
    reset_task(task)
    product = db.get(TemuProduct, task.product_id)
    if product:
        product.image_status = "pending"
    db.commit()
    row = db.execute(
        select(TemuImageDownloadTask, TemuProduct, AdminUser, TemuProductGroup)
        .join(TemuProduct, TemuProduct.id == TemuImageDownloadTask.product_id)
        .outerjoin(AdminUser, AdminUser.id == TemuImageDownloadTask.owner_id)
        .outerjoin(TemuProductGroup, TemuProductGroup.id == TemuImageDownloadTask.group_id)
        .where(TemuImageDownloadTask.id == task_id)
    ).one()
    return task_response(*row)


@router.post("/tasks/retry-failed", response_model=dict[str, int])
def retry_failed(
    _: Annotated[AdminUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    owner_id: int | None = Query(default=None, gt=0),
) -> dict[str, int]:
    statement = select(TemuImageDownloadTask).where(TemuImageDownloadTask.status == "failed")
    if owner_id:
        statement = statement.where(TemuImageDownloadTask.owner_id == owner_id)
    tasks = list(db.scalars(statement))
    for task in tasks:
        reset_task(task)
        product = db.get(TemuProduct, task.product_id)
        if product:
            product.image_status = "pending"
    db.commit()
    return {"retried": len(tasks)}
