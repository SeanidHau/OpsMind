"""Prometheus 只读查询工具。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.models.contracts import ToolDefinition, ToolRiskLevel
from app.tools.registry import ToolExecutionError, ToolRegistry

MAX_QUERY_LENGTH = 1_000
MAX_RESPONSE_BYTES = 512 * 1024
MAX_RESULT_SAMPLES = 50


class PrometheusClient:
    """以 Prometheus Query API 获取有限的实时只读指标。"""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        fetcher: Callable[[Request], bytes] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._fetcher = fetcher or self._fetch

    def query(self, promql: str) -> dict[str, Any]:
        """执行瞬时查询，并限制传回 Agent 的结果规模。"""
        normalized_query = promql.strip()
        if not normalized_query or len(normalized_query) > MAX_QUERY_LENGTH:
            raise ToolExecutionError("PromQL query must be between 1 and 1000 characters")
        request = Request(f"{self._base_url}/api/v1/query?{urlencode({'query': normalized_query})}")
        if self._bearer_token is not None:
            request.add_header("Authorization", f"Bearer {self._bearer_token}")
        try:
            payload = json.loads(self._fetcher(request))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ToolExecutionError("Prometheus query failed") from error
        if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
            raise ToolExecutionError("Prometheus returned an unsuccessful response")
        data = payload["data"]
        result = data.get("result")
        if not isinstance(result, list):
            raise ToolExecutionError("Prometheus response has an invalid result")
        return {
            "query": normalized_query,
            "result_type": str(data.get("resultType", "unknown")),
            "samples": result[:MAX_RESULT_SAMPLES],
        }

    @staticmethod
    def _fetch(request: Request) -> bytes:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - endpoint is explicit config
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        if not isinstance(payload, bytes):
            raise OSError("Prometheus response is not bytes")
        return payload[:MAX_RESPONSE_BYTES]


def register_prometheus_tools(registry: ToolRegistry, client: PrometheusClient) -> None:
    """注册一个仅调用 Prometheus Query API 的低风险工具。"""

    async def query_prometheus(args: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(client.query, str(args["query"]))

    registry.register(
        ToolDefinition(
            name="query_prometheus",
            description="使用 PromQL 查询已配置 Prometheus 中的实时指标。",
            risk_level=ToolRiskLevel.LOW,
            read_only=True,
            required_args=("query",),
            allowed_args=("query",),
            max_calls_per_run=2,
        ),
        query_prometheus,
    )
