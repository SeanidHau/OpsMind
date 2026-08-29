"""Prometheus 只读工具验收测试。"""

import json

import pytest

from app.models.contracts import ActionType, AgentAction
from app.tools.prometheus import PrometheusClient, register_prometheus_tools
from app.tools.registry import ToolExecutionError, ToolRegistry


def prometheus_action(query: str) -> AgentAction:
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="读取实时错误率",
        tool_name="query_prometheus",
        tool_args={"query": query},
        reason="需要实时指标作为诊断证据。",
    )


@pytest.mark.asyncio
async def test_prometheus_tool_returns_bounded_read_only_samples() -> None:
    """工具仅调用 Query API，并限制返回给 Agent 的样本数量。"""
    requests = []

    def fetcher(request):
        requests.append(request.full_url)
        return json.dumps(
            {
                "status": "success",
                "data": {"resultType": "vector", "result": [{"metric": {"job": "pay"}}] * 60},
            }
        ).encode()

    registry = ToolRegistry()
    register_prometheus_tools(
        registry, PrometheusClient(base_url="http://prometheus.local", fetcher=fetcher)
    )

    result = await registry.execute(prometheus_action("up{job='payment'}"))

    assert result["result_type"] == "vector"
    assert len(result["samples"]) == 50
    assert requests == ["http://prometheus.local/api/v1/query?query=up%7Bjob%3D%27payment%27%7D"]


def test_prometheus_client_rejects_unbounded_query_text() -> None:
    """在发起网络请求前拒绝异常长的 PromQL。"""
    client = PrometheusClient(base_url="http://prometheus.local", fetcher=lambda _: b"{}")

    with pytest.raises(ToolExecutionError, match="1000"):
        client.query("x" * 1_001)
