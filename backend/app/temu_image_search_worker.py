from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import get_settings
from .database import SessionLocal
from .models import TemuImageSearchTask, TemuProduct
from .temu_image_search_client import (
    ImageSearchApiError,
    request_json,
)
from .temu_image_search_services import (
    claim_temu_image_search,
    configured_search_api_key,
    image_search_config,
    replace_image_search_result,
    resolve_search_image_url,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
MAX_ATTEMPTS = 5
TERMINAL_FAILURE_STATUSES = {"failed", "cancelled"}
RETRYABLE_REMOTE_CODES = {
    "RATE_LIMITED",
    "TASK_TIMEOUT",
    "INTERNAL_ERROR",
    "MTOP_NETWORK_ERROR",
}


class QueueInterrupted(RuntimeError):
    def __init__(self, state: str):
        super().__init__(f"图搜队列已{state}")
        self.state = state


def current_queue_control() -> tuple[str, int, int]:
    with SessionLocal() as db:
        config = image_search_config(db)
        return (
            config.queue_state,
            config.queue_revision,
            config.concurrency,
        )


def ensure_queue_running(
    expected_revision: int | None = None,
) -> None:
    state, revision, _ = current_queue_control()
    if state != "running" or (
        expected_revision is not None
        and revision != expected_revision
    ):
        raise QueueInterrupted(
            state if state != "running" else "已切换"
        )


def remote_task_error(
    payload: dict[str, Any],
    default_message: str,
) -> ImageSearchApiError:
    error = payload.get("error") or {}
    code = str(error.get("code") or "REMOTE_FAILED")
    return ImageSearchApiError(
        code,
        str(error.get("message") or default_message),
        retryable=code in RETRYABLE_REMOTE_CODES,
    )


async def wait_remote_task(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    poll_interval: int,
    deadline: float,
    queue_revision: int,
    default_failure_message: str,
) -> dict[str, Any]:
    while asyncio.get_running_loop().time() < deadline:
        ensure_queue_running(queue_revision)
        payload = await request_json(
            client,
            "GET",
            url,
            headers=headers,
        )
        ensure_queue_running(queue_revision)
        status = payload.get("status")
        if status == "succeeded":
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ImageSearchApiError(
                    "INVALID_RESPONSE",
                    "成功任务缺少 result",
                )
            return result
        if status in TERMINAL_FAILURE_STATUSES:
            raise remote_task_error(
                payload,
                default_failure_message,
            )
        if status not in {"queued", "running"}:
            raise ImageSearchApiError(
                "INVALID_STATUS",
                f"未知图搜任务状态：{status}",
            )
        await asyncio.sleep(poll_interval)
    raise ImageSearchApiError(
        "TASK_TIMEOUT",
        "等待图搜任务完成超时",
        retryable=True,
    )


async def execute_remote_search(
    task_id: int,
) -> tuple[dict[str, Any], int]:
    with SessionLocal() as db:
        task = db.get(TemuImageSearchTask, task_id)
        config = image_search_config(db)
        if not task or not config:
            raise ImageSearchApiError(
                "LOCAL_TASK_MISSING",
                "本地图搜任务或配置不存在",
            )
        if config.queue_state != "running":
            raise QueueInterrupted(config.queue_state)
        queue_revision = config.queue_revision
        product = db.get(TemuProduct, task.product_id)
        if not product:
            raise ImageSearchApiError(
                "LOCAL_PRODUCT_MISSING",
                "Temu 商品不存在",
            )
        api_key = configured_search_api_key(config, settings)
        base_url = config.base_url.rstrip("/")
        timeout_seconds = config.timeout_seconds
        poll_interval = config.poll_interval_seconds
        task_timeout = config.task_timeout_seconds
        image_url, image_source = resolve_search_image_url(
            product,
            config,
            settings.image_storage_path,
        )
        task.request_image_url = image_url
        task.request_image_source = image_source
        upload_task_id = task.upload_task_id
        product_task_id = task.product_task_id
        db.commit()

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    poll_headers = {"X-API-Key": api_key}
    deadline = asyncio.get_running_loop().time() + task_timeout

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        if not upload_task_id:
            ensure_queue_running(queue_revision)
            created = await request_json(
                client,
                "POST",
                f"{base_url}/api/v1/upload-tasks",
                headers=headers,
                json={"image_url": image_url},
            )
            ensure_queue_running(queue_revision)
            upload_task_id = created.get("task_id")
            if not upload_task_id:
                raise ImageSearchApiError(
                    "INVALID_RESPONSE",
                    "创建上传任务未返回 task_id",
                )
            upload_task_id = str(upload_task_id)
            with SessionLocal() as db:
                task = db.get(TemuImageSearchTask, task_id)
                if task:
                    task.upload_task_id = upload_task_id
                    task.remote_stage = "upload"
                    db.commit()

        if not product_task_id:
            upload_result = await wait_remote_task(
                client,
                url=(
                    f"{base_url}/api/v1/upload-tasks/"
                    f"{upload_task_id}"
                ),
                headers=poll_headers,
                poll_interval=poll_interval,
                deadline=deadline,
                queue_revision=queue_revision,
                default_failure_message="1688 图片上传任务失败",
            )
            if not upload_result.get("image_id"):
                raise ImageSearchApiError(
                    "UPLOAD_NO_IMAGE_ID",
                    "上传任务结果缺少 image_id",
                )
            if not upload_result.get("search_page_url"):
                raise ImageSearchApiError(
                    "INVALID_RESPONSE",
                    "上传任务结果缺少 search_page_url",
                )
            ensure_queue_running(queue_revision)
            created = await request_json(
                client,
                "POST",
                f"{base_url}/api/v1/product-tasks",
                headers=headers,
                json={"upload_task_id": upload_task_id},
            )
            ensure_queue_running(queue_revision)
            product_task_id = created.get("task_id")
            if not product_task_id:
                raise ImageSearchApiError(
                    "INVALID_RESPONSE",
                    "创建商品任务未返回 task_id",
                )
            product_task_id = str(product_task_id)
            with SessionLocal() as db:
                task = db.get(TemuImageSearchTask, task_id)
                if task:
                    task.product_task_id = product_task_id
                    task.remote_stage = "product"
                    db.commit()

        result = await wait_remote_task(
            client,
            url=(
                f"{base_url}/api/v1/product-tasks/"
                f"{product_task_id}"
            ),
            headers=poll_headers,
            poll_interval=poll_interval,
            deadline=deadline,
            queue_revision=queue_revision,
            default_failure_message="1688 商品查询任务失败",
        )
        ensure_queue_running(queue_revision)
        return result, queue_revision


async def process_search(task_id: int) -> None:
    try:
        result_payload, queue_revision = await execute_remote_search(
            task_id
        )
        ensure_queue_running(queue_revision)
        with SessionLocal() as db:
            task = db.get(TemuImageSearchTask, task_id)
            config = image_search_config(db)
            if not task:
                return
            if (
                config.queue_state != "running"
                or config.queue_revision != queue_revision
            ):
                raise QueueInterrupted(config.queue_state)
            replace_image_search_result(db, task, result_payload)
            task.status = "completed"
            task.remote_stage = "completed"
            task.last_error_code = None
            task.last_error = None
            task.locked_at = None
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    except QueueInterrupted as error:
        logger.info(
            "Temu image-search task %s interrupted by queue state %s",
            task_id,
            error.state,
        )
        with SessionLocal() as db:
            task = db.get(TemuImageSearchTask, task_id)
            if not task:
                return
            task.status = "pending"
            task.locked_at = None
            task.available_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as error:
        logger.exception(
            "Temu image-search task %s failed: %s",
            task_id,
            error,
        )
        with SessionLocal() as db:
            task = db.get(TemuImageSearchTask, task_id)
            if not task:
                return
            code = (
                error.code
                if isinstance(error, ImageSearchApiError)
                else type(error).__name__
            )
            retryable = (
                isinstance(error, ImageSearchApiError)
                and error.retryable
            )
            task.last_error_code = code[:80]
            task.last_error = str(error)[:4000]
            task.locked_at = None
            if code in RETRYABLE_REMOTE_CODES:
                task.remote_stage = None
                task.upload_task_id = None
                task.product_task_id = None
            if retryable and task.attempts < MAX_ATTEMPTS:
                task.status = "pending"
                task.available_at = (
                    datetime.now(timezone.utc)
                    + timedelta(
                        seconds=min(
                            300,
                            2 ** task.attempts * 5,
                        )
                    )
                )
            else:
                task.status = "failed"
                task.finished_at = datetime.now(timezone.utc)
            db.commit()


async def run() -> None:
    running: set[asyncio.Task] = set()
    while True:
        running = {
            task for task in running if not task.done()
        }
        state, _, concurrency = current_queue_control()
        if state != "running":
            await asyncio.sleep(1)
            continue
        while len(running) < concurrency:
            with SessionLocal() as db:
                task = claim_temu_image_search(db)
            if task is None:
                break
            running.add(
                asyncio.create_task(process_search(task.id))
            )
        await asyncio.sleep(1 if running else 3)


if __name__ == "__main__":
    asyncio.run(run())
