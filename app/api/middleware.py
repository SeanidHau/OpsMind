"""HTTP 请求上下文中间件。"""

from __future__ import annotations

import time
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128

logger = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求建立关联 ID，并记录结构化访问日志。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """绑定请求上下文，执行下游应用并回传关联 ID。"""
        request_id = self._resolve_request_id(request)
        started_at = time.perf_counter()

        # 每个请求开始前清理旧上下文，避免复用线程时串联字段。
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )

        try:
            response = await call_next(request)
            latency_ms = round((time.perf_counter() - started_at) * 1_000)

            # 先记录日志；此时 request_id、方法和路径仍在上下文中。
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "http_request_completed",
                status_code=response.status_code,
                latency_ms=latency_ms,
            )
            return response
        finally:
            # 成功和异常路径都清理全部字段，避免上下文泄漏到后续请求。
            structlog.contextvars.clear_contextvars()

    @staticmethod
    def _resolve_request_id(request: Request) -> str:
        """复用安全的客户端关联 ID；无效值改为服务端生成。"""
        candidate = request.headers.get(REQUEST_ID_HEADER)

        if (
            candidate is not None
            and 0 < len(candidate) <= MAX_REQUEST_ID_LENGTH
            and all(character.isalnum() or character in "-_" for character in candidate)
        ):
            return candidate

        # 使用无连字符 UUID，便于日志检索和响应头传输。
        return uuid4().hex
