"""通过 MCP 暴露各观测系统的受限只读查询能力。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP

from app.config import Settings, get_settings

MAX_RESPONSE_BYTES = 512 * 1024
MAX_QUERY_LENGTH = 1_000
MAX_RESULT_ITEMS = 50
KUBERNETES_RESOURCES = {"pods", "services", "deployments", "events"}

mcp = FastMCP(
    "OpsMind Observability",
    instructions="只提供受限、只读的 Prometheus、Loki、Jaeger、Kubernetes 与 CMDB 查询。",
    json_response=True,
)


def _secret_value(value: Any) -> str | None:
    """读取可选 SecretStr，避免在工具结果或日志中返回凭据。"""
    return value.get_secret_value() if value is not None else None


def _request_json(url: str, bearer_token: str | None = None) -> Any:
    """执行受大小限制的 JSON GET 请求。"""
    request = Request(url)
    if bearer_token is not None:
        request.add_header("Authorization", f"Bearer {bearer_token}")
    with urlopen(request, timeout=5) as response:  # noqa: S310 - 地址来自受控配置
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("上游响应超过允许大小")
    return json.loads(payload)


def _configured_url(settings: Settings, field_name: str, system_name: str) -> str:
    """为未配置的外部系统返回对用户和 Agent 都清晰的错误。"""
    url = getattr(settings, field_name)
    if url is None:
        raise ValueError(f"未配置 {system_name} 地址")
    return str(url).rstrip("/")


def _require_short_text(value: str, field_name: str) -> str:
    """拒绝空值与异常长查询，避免将 MCP 变成任意请求转发器。"""
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_QUERY_LENGTH:
        raise ValueError(f"{field_name} 长度必须在 1 到 {MAX_QUERY_LENGTH} 个字符之间")
    return normalized


@mcp.tool()
def query_prometheus(query: str) -> dict[str, Any]:
    """使用 PromQL 查询当前 Prometheus 指标，仅返回有限样本。"""
    settings = get_settings()
    normalized_query = _require_short_text(query, "PromQL")
    base_url = _configured_url(settings, "prometheus_url", "Prometheus")
    payload = _request_json(
        f"{base_url}/api/v1/query?{urlencode({'query': normalized_query})}",
        _secret_value(settings.prometheus_bearer_token),
    )
    if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise ValueError("Prometheus 返回了非成功响应")
    data = payload["data"]
    result = data.get("result")
    if not isinstance(result, list):
        raise ValueError("Prometheus 响应格式无效")
    return {
        "query": normalized_query,
        "result_type": str(data.get("resultType", "unknown")),
        "samples": result[:MAX_RESULT_ITEMS],
    }


@mcp.tool()
def query_loki(query: str, limit: int = 50) -> dict[str, Any]:
    """使用 LogQL 查询 Loki 日志，仅返回有限日志流。"""
    settings = get_settings()
    normalized_query = _require_short_text(query, "LogQL")
    base_url = _configured_url(settings, "loki_url", "Loki")
    safe_limit = min(max(limit, 1), MAX_RESULT_ITEMS)
    query_string = urlencode({"query": normalized_query, "limit": safe_limit})
    payload = _request_json(
        f"{base_url}/loki/api/v1/query?{query_string}",
        _secret_value(settings.loki_bearer_token),
    )
    if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise ValueError("Loki 返回了非成功响应")
    return {"query": normalized_query, "result": payload["data"].get("result", [])[:safe_limit]}


@mcp.tool()
def query_jaeger(service: str, limit: int = 20) -> dict[str, Any]:
    """按服务名查询 Jaeger trace，仅返回有限条记录。"""
    settings = get_settings()
    normalized_service = _require_short_text(service, "服务名")
    base_url = _configured_url(settings, "jaeger_url", "Jaeger")
    safe_limit = min(max(limit, 1), MAX_RESULT_ITEMS)
    payload = _request_json(
        f"{base_url}/api/traces?{urlencode({'service': normalized_service, 'limit': safe_limit})}",
        _secret_value(settings.jaeger_bearer_token),
    )
    traces = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(traces, list):
        raise ValueError("Jaeger 响应格式无效")
    return {"service": normalized_service, "traces": traces[:safe_limit]}


@mcp.tool()
def query_kubernetes(namespace: str, resource: str = "pods") -> dict[str, Any]:
    """读取一个命名空间中的有限 Kubernetes 资源。"""
    settings = get_settings()
    normalized_namespace = _require_short_text(namespace, "命名空间")
    normalized_resource = resource.strip().lower()
    if normalized_resource not in KUBERNETES_RESOURCES:
        allowed = ", ".join(sorted(KUBERNETES_RESOURCES))
        raise ValueError(f"resource 仅支持：{allowed}")
    base_url = _configured_url(settings, "kubernetes_url", "Kubernetes")
    api_group = (
        "api/v1" if normalized_resource in {"pods", "services", "events"} else "apis/apps/v1"
    )
    payload = _request_json(
        f"{base_url}/{api_group}/namespaces/{normalized_namespace}/{normalized_resource}",
        _secret_value(settings.kubernetes_bearer_token),
    )
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        raise ValueError("Kubernetes 响应格式无效")
    return {
        "namespace": normalized_namespace,
        "resource": normalized_resource,
        "items": items[:MAX_RESULT_ITEMS],
    }


@mcp.tool()
def query_cmdb(service: str) -> dict[str, Any]:
    """按服务名查询 CMDB 中的服务与依赖信息。"""
    settings = get_settings()
    normalized_service = _require_short_text(service, "服务名")
    base_url = _configured_url(settings, "cmdb_url", "CMDB")
    payload = _request_json(
        f"{base_url}/api/v1/services?{urlencode({'name': normalized_service})}",
        _secret_value(settings.cmdb_bearer_token),
    )
    return {"service": normalized_service, "record": payload}


if __name__ == "__main__":
    mcp.run(transport="stdio")
