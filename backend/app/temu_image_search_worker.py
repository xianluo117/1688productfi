from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import get_settings
from .database import SessionLocal
from .models import TemuImageSearchTask
from .temu_image_search_services import (
    claim_temu_image_search,
    configured_search_api_key,
    image_search_config,
    replace_image_search_result,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
MAX_ATTEMPTS = 5


class ImageSearchError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def response_error(response: httpx.Response) -> ImageSearchError:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else None
    resolved_code = code or f"HTTP_{response.status_code}"
    resolved_message = message or response.text.strip()[:2000] or response.reason_phrase
    return ImageSearchError(
        resolved_code,
        resolved_message,
        retryable=response.status_code == 429 or response.status_code >= 500,
    )


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.request(method, url, headers=headers, json=json)
    except httpx.TimeoutException as error:
        raise ImageSearchError("HTTP_TIMEOUT", "图搜 API 请求超时", retryable=True) from error
    except httpx.HTTPError as error:
        raise ImageSearchError(
            "HTTP_CONNECTION_ERROR", f"图搜 API 连接失败：{error}", retryable=True
        ) from error
    if not response.is_success:
        raise response_error(response)
    try:
        payload = response.json()
    except ValueError as error:
        raise ImageSearchError("INVALID_JSON", "图搜 API 返回了无效 JSON") from error
    if not isinstance(payload, dict):
        raise ImageSearchError("INVALID_RESPONSE", "图搜 API 返回结构无效")
    return payload


async def execute_remote_search(task_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        task = db.get(TemuImageSearchTask, task_id)
        config = image_search_config(db)
        if not task or not config:
            raise ImageSearchError("LOCAL_TASK_MISSING", "本地图搜任务或配置不存在")
        api_key = configured_search_api_key(config, settings)
        base_url = config.base_url.rstrip("/")
        timeout_seconds = config.timeout_seconds
        poll_interval = config.poll_interval_seconds
        task_timeout = config.task_timeout_seconds
        image_url = task.image_url
        remote_task_id = task.remote_task_id

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        if not remote_task_id:
            created = await request_json(
                client,
                "POST",
                f"{base_url}/api/v1/search-tasks",
                headers=headers,
                json={"image_url": image_url},
            )
            remote_task_id = created.get("task_id")
            if not remote_task_id:
                raise ImageSearchError("INVALID_RESPONSE", "创建图搜任务未返回 task_id")
            with SessionLocal() as db:
                task = db.get(TemuImageSearchTask, task_id)
                if task:
                    task.remote_task_id = str(remote_task_id)
                    db.commit()

        deadline = asyncio.get_running_loop().time() + task_timeout
        while asyncio.get_running_loop().time() < deadline:
            payload = await request_json(
                client,
                "GET",
                f"{base_url}/api/v1/search-tasks/{remote_task_id}",
                headers={"X-API-Key": api_key},
            )
            status = payload.get("status")
            if status == "succeeded":
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise ImageSearchError("INVALID_RESPONSE", "成功任务缺少 result")
                return result
            if status == "failed":
                error = payload.get("error") or {}
                raise ImageSearchError(
                    str(error.get("code") or "REMOTE_FAILED"),
                    str(error.get("message") or "1688 图搜任务失败"),
                    retryable=str(error.get("code") or "")
                    in {"RATE_LIMITED", "TASK_TIMEOUT", "INTERNAL_ERROR"},
                )
            if status not in {"queued", "running"}:
                raise ImageSearchError("INVALID_STATUS", f"未知图搜任务状态：{status}")
            await asyncio.sleep(poll_interval)
    raise ImageSearchError("TASK_TIMEOUT", "等待图搜任务完成超时", retryable=True)


async def process_search(task_id: int) -> None:
    try:
        result_payload = await execute_remote_search(task_id)
        with SessionLocal() as db:
            task = db.get(TemuImageSearchTask, task_id)
            if not task:
                return
            replace_image_search_result(db, task, result_payload)
            task.status = "completed"
            task.last_error_code = None
            task.last_error = None
            task.locked_at = None
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as error:
        logger.exception("Temu image-search task %s failed: %s", task_id, error)
        with SessionLocal() as db:
            task = db.get(TemuImageSearchTask, task_id)
            if not task:
                return
            code = error.code if isinstance(error, ImageSearchError) else type(error).__name__
            retryable = isinstance(error, ImageSearchError) and error.retryable
            task.last_error_code = code[:80]
            task.last_error = str(error)[:4000]
            task.locked_at = None
            if code in {"RATE_LIMITED", "TASK_TIMEOUT", "INTERNAL_ERROR"}:
                task.remote_task_id = None
            if retryable and task.attempts < MAX_ATTEMPTS:
                task.status = "pending"
                task.available_at = datetime.now(timezone.utc) + timedelta(
                    seconds=min(300, 2 ** task.attempts * 5)
                )
            else:
                task.status = "failed"
                task.finished_at = datetime.now(timezone.utc)
            db.commit()


async def run() -> None:
    running: set[asyncio.Task] = set()
    while True:
        running = {task for task in running if not task.done()}
        while len(running) < 4:
            with SessionLocal() as db:
                task = claim_temu_image_search(db)
            if task is None:
                break
            running.add(asyncio.create_task(process_search(task.id)))
        await asyncio.sleep(1 if running else 3)


if __name__ == "__main__":
    asyncio.run(run())
