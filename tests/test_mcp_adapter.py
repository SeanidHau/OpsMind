"""MCP Tool Adapter 的注册与调用验收。"""

from typing import Any

import pytest

from app.models.contracts import ActionType, AgentAction
from app.tools.mcp_adapter import register_mcp_observability_tools
from app.tools.registry import ToolRegistry


def tool_action(name: str, args: dict[str, Any]) -> AgentAction:
    """构造一条由 Harness 发出的只读工具动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="收集实时诊断证据",
        tool_name=name,
        tool_args=args,
        reason="需要外部系统的只读数据。",
    )


@pytest.mark.asyncio
async def test_mcp_adapter_registers_all_observability_tools() -> None:
    """五类系统都通过本地 Adapter 暴露给 Harness，且参数边界可校验。"""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(name: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, args))
        return {"source": "mcp", "tool": name}

    registry = ToolRegistry()
    register_mcp_observability_tools(registry, invoke)

    assert [definition.name for definition in registry.definitions()] == [
        "query_cmdb",
        "query_jaeger",
        "query_kubernetes",
        "query_loki",
        "query_prometheus",
    ]
    result = await registry.execute(tool_action("query_loki", {"query": '{service="payment"}'}))

    assert result == {"source": "mcp", "tool": "query_loki"}
    assert calls == [("query_loki", {"query": '{service="payment"}'})]


@pytest.mark.asyncio
async def test_mcp_adapter_keeps_kubernetes_queries_read_only_and_bounded() -> None:
    """资源类型仍受 ToolRegistry schema 保护，不会接受任意参数。"""

    async def invoke(_: str, __: dict[str, Any]) -> dict[str, Any]:
        return {}

    registry = ToolRegistry()
    register_mcp_observability_tools(registry, invoke)

    result = await registry.execute(
        tool_action("query_kubernetes", {"namespace": "production", "resource": "pods"})
    )

    assert result == {}
    definition = next(item for item in registry.definitions() if item.name == "query_kubernetes")
    assert definition.read_only is True
    assert definition.max_calls_per_run == 2


def test_mcp_adapter_only_registers_configured_tools() -> None:
    """未配置的数据源不应成为模型可选工具。"""

    async def invoke(_: str, __: dict[str, Any]) -> dict[str, Any]:
        return {}

    registry = ToolRegistry()
    register_mcp_observability_tools(
        registry,
        invoke,
        available_tools={"query_prometheus"},
    )

    assert [definition.name for definition in registry.definitions()] == ["query_prometheus"]
