"""将 MCP 工具适配为 OpsMind Harness 的本地 ToolRegistry 工具。"""

from __future__ import annotations

import shlex
from collections.abc import Awaitable
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.models.contracts import ToolDefinition, ToolRiskLevel
from app.tools.registry import ToolExecutionError, ToolRegistry


class McpToolInvoker(Protocol):
    """可替换的 MCP 调用边界，便于在不启动子进程时测试适配规则。"""

    def __call__(self, tool_name: str, args: dict[str, Any]) -> Awaitable[dict[str, Any]]:
        """调用 MCP 工具并将结果规范为字典。"""


class StdioMcpToolInvoker:
    """按一次调用一个短连接的方式运行本地 stdio MCP Server。"""

    def __init__(
        self,
        *,
        command: str,
        arguments: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self._params = StdioServerParameters(
            command=command,
            args=shlex.split(arguments or ""),
            env=environment,
        )

    async def __call__(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """启动受配置控制的 MCP Server 并调用一个工具。"""
        try:
            async with stdio_client(self._params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, args)
        except (OSError, RuntimeError, ValueError) as error:
            raise ToolExecutionError(f"MCP tool {tool_name} is unavailable") from error

        if result.isError:
            raise ToolExecutionError(f"MCP tool {tool_name} returned an error")
        if result.structuredContent is not None:
            return dict(result.structuredContent)
        return {"content": [item.model_dump(mode="json") for item in result.content]}


def register_mcp_observability_tools(registry: ToolRegistry, invoker: McpToolInvoker) -> None:
    """注册经 MCP 调用的五个受限、只读观测工具。"""

    definitions = (
        ToolDefinition(
            name="query_prometheus",
            description="通过 MCP 使用 PromQL 查询已配置 Prometheus 的实时指标。",
            risk_level=ToolRiskLevel.LOW,
            read_only=True,
            required_args=("query",),
            allowed_args=("query",),
            max_calls_per_run=2,
        ),
        ToolDefinition(
            name="query_loki",
            description="通过 MCP 使用 LogQL 查询已配置 Loki 的近期日志。",
            risk_level=ToolRiskLevel.LOW,
            read_only=True,
            required_args=("query",),
            allowed_args=("query", "limit"),
            max_calls_per_run=2,
        ),
        ToolDefinition(
            name="query_jaeger",
            description="通过 MCP 按服务名查询已配置 Jaeger 的调用链。",
            risk_level=ToolRiskLevel.LOW,
            read_only=True,
            required_args=("service",),
            allowed_args=("service", "limit"),
            max_calls_per_run=2,
        ),
        ToolDefinition(
            name="query_kubernetes",
            description="通过 MCP 读取命名空间中的有限 Kubernetes 资源。",
            risk_level=ToolRiskLevel.LOW,
            read_only=True,
            required_args=("namespace",),
            allowed_args=("namespace", "resource"),
            max_calls_per_run=2,
        ),
        ToolDefinition(
            name="query_cmdb",
            description="通过 MCP 按服务名读取 CMDB 服务与依赖信息。",
            risk_level=ToolRiskLevel.LOW,
            read_only=True,
            required_args=("service",),
            allowed_args=("service",),
            max_calls_per_run=2,
        ),
    )

    for definition in definitions:

        async def handler(
            args: dict[str, Any], *, tool_name: str = definition.name
        ) -> dict[str, Any]:
            return await invoker(tool_name, args)

        registry.register(definition, handler)
