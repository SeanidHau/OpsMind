"""内置观测 MCP Server 的纯函数边界测试。"""

import pytest

from app.mcp.observability_server import KUBERNETES_RESOURCES, _require_short_text


def test_mcp_server_rejects_blank_or_oversized_query_text() -> None:
    """Server 不接受空查询或可用于放大请求的超长输入。"""
    with pytest.raises(ValueError, match="1 到 1000"):
        _require_short_text(" ", "PromQL")
    with pytest.raises(ValueError, match="1 到 1000"):
        _require_short_text("x" * 1_001, "PromQL")


def test_mcp_server_has_a_small_kubernetes_resource_allowlist() -> None:
    """Kubernetes MCP 工具只暴露诊断所需的只读资源。"""
    assert KUBERNETES_RESOURCES == {"pods", "services", "deployments", "events"}
