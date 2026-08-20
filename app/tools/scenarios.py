"""可复现故障场景与只读诊断工具。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.models.contracts import IncidentScenario, ToolDefinition, ToolRiskLevel
from app.tools.registry import ToolExecutionError, ToolRegistry


class ScenarioStore:
    """按服务名称读取固定故障场景。"""

    def __init__(self, scenarios: Iterable[IncidentScenario]) -> None:
        self._scenarios: dict[str, IncidentScenario] = {}

        for scenario in scenarios:
            if scenario.service in self._scenarios:
                raise ValueError(f"duplicate scenario service: {scenario.service}")
            self._scenarios[scenario.service] = scenario

    def get(self, service: str) -> IncidentScenario:
        """返回指定服务的场景；不存在时拒绝伪造空证据。"""
        scenario = self._scenarios.get(service)
        if scenario is None:
            raise ToolExecutionError(f"scenario not found: {service}")
        return scenario


def register_scenario_tools(registry: ToolRegistry, store: ScenarioStore) -> None:
    """向注册表添加日志、指标和拓扑三个低风险只读工具。"""

    async def query_logs(args: dict[str, Any]) -> dict[str, Any]:
        """按可选关键词筛选固定场景日志。"""
        scenario = store.get(str(args["service"]))
        contains = str(args.get("contains", "")).casefold()

        logs = [log for log in scenario.logs if not contains or contains in log.message.casefold()]

        return {
            "service": scenario.service,
            "count": len(logs),
            "logs": [log.model_dump(mode="json") for log in logs],
        }

    async def query_metrics(args: dict[str, Any]) -> dict[str, Any]:
        """返回固定场景的数值指标。"""
        scenario = store.get(str(args["service"]))

        return {
            "service": scenario.service,
            # 返回副本，避免调用方修改场景原始数据。
            "metrics": dict(scenario.metrics),
        }

    async def query_topology(args: dict[str, Any]) -> dict[str, Any]:
        """返回固定场景的下游依赖关系。"""
        scenario = store.get(str(args["service"]))

        return {
            "service": scenario.service,
            "dependencies": list(scenario.dependencies),
        }

    registry.register(
        ToolDefinition(
            name="query_logs",
            description="查询指定服务的预设日志。",
            risk_level=ToolRiskLevel.LOW,
            required_args=("service",),
            allowed_args=("service", "contains"),
        ),
        query_logs,
    )
    registry.register(
        ToolDefinition(
            name="query_metrics",
            description="查询指定服务的预设指标。",
            risk_level=ToolRiskLevel.LOW,
            required_args=("service",),
            allowed_args=("service",),
        ),
        query_metrics,
    )
    registry.register(
        ToolDefinition(
            name="query_topology",
            description="查询指定服务的预设依赖关系。",
            risk_level=ToolRiskLevel.LOW,
            required_args=("service",),
            allowed_args=("service",),
        ),
        query_topology,
    )
