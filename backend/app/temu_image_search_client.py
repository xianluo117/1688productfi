from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ImageSearchApiError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def response_error(response: httpx.Response) -> ImageSearchApiError:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else None
    resolved_code = code or f"HTTP_{response.status_code}"
    resolved_message = (
        message
        or response.text.strip()[:2000]
        or response.reason_phrase
    )
    return ImageSearchApiError(
        resolved_code,
        resolved_message,
        retryable=(
            response.status_code == 429
            or response.status_code >= 500
        ),
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
        response = await client.request(
            method,
            url,
            headers=headers,
            json=json,
        )
    except httpx.TimeoutException as error:
        raise ImageSearchApiError(
            "HTTP_TIMEOUT",
            "图搜 API 请求超时",
            retryable=True,
        ) from error
    except httpx.HTTPError as error:
        raise ImageSearchApiError(
            "HTTP_CONNECTION_ERROR",
            f"图搜 API 连接失败：{error}",
            retryable=True,
        ) from error
    if not response.is_success:
        raise response_error(response)
    try:
        payload = response.json()
    except ValueError as error:
        raise ImageSearchApiError(
            "INVALID_JSON",
            "图搜 API 返回了无效 JSON",
        ) from error
    if not isinstance(payload, dict):
        raise ImageSearchApiError(
            "INVALID_RESPONSE",
            "图搜 API 返回结构无效",
        )
    return payload


async def cancel_remote_tasks(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: int,
    upload_task_id: str | None,
    product_task_id: str | None,
) -> list[str]:
    failures: list[str] = []
    headers = {"X-API-Key": api_key}
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for task_type, task_id in (
            ("product-tasks", product_task_id),
            ("upload-tasks", upload_task_id),
        ):
            if not task_id:
                continue
            try:
                await request_json(
                    client,
                    "DELETE",
                    f"{base_url.rstrip('/')}/api/v1/{task_type}/{task_id}",
                    headers=headers,
                )
            except Exception as error:
                message = f"{task_type}/{task_id}: {error}"
                failures.append(message)
                logger.warning("取消远程图搜任务失败：%s", message)
    return failures
