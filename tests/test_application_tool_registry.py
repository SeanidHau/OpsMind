"""应用工厂的场景工具装配验收测试。"""

import pytest
from starlette.requests import Request

from app.api.dependencies import get_tool_registry
from app.api.main import create_app
from app.models.contracts import ActionType, AgentAction, IncidentScenario, ScenarioLog
from app.tools.registry import ToolRegistry
from app.tools.scenarios import ScenarioStore


def make_store(*, service: str, error_rate: float) -> ScenarioStore:
    """构造能区分应用实例数据来源的最小场景存储。"""
    return ScenarioStore(
        [
            IncidentScenario(
                scenario_id=f"{service}-scenario",
                service=service,
                logs=[
                    ScenarioLog(
                        timestamp="2026-08-27T10:00:00Z",
                        level="ERROR",
                        message="fixed diagnostic evidence",
                    )
                ],
                metrics={"error_rate": error_rate},
                dependencies=["postgres-primary"],
            )
        ]
    )


def metrics_action(service: str) -> AgentAction:
    """构造已由注册表定义允许的只读指标查询动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="读取场景指标",
        tool_name="query_metrics",
        tool_args={"service": service},
        reason="收集诊断证据",
    )


@pytest.mark.asyncio
async def test_application_builds_registry_bound_to_injected_scenario_store() -> None:
    """应用注册的工具必须从同一个注入场景存储读取数据。"""
    app = create_app(scenario_store=make_store(service="checkout-service", error_rate=0.24))

    registry = app.state.tool_registry

    assert isinstance(registry, ToolRegistry)
    assert {policy.name for policy in registry.policies()} == {
        "query_logs",
        "query_metrics",
        "query_topology",
    }
    assert await registry.execute(metrics_action("checkout-service")) == {
        "service": "checkout-service",
        "metrics": {"error_rate": 0.24},
    }


@pytest.mark.asyncio
async def test_application_instances_do_not_share_scenario_tool_registries() -> None:
    """两个应用实例的工具注册表和工具数据必须彼此隔离。"""
    first_app = create_app(scenario_store=make_store(service="first-service", error_rate=0.11))
    second_app = create_app(scenario_store=make_store(service="second-service", error_rate=0.22))

    first_registry = first_app.state.tool_registry
    second_registry = second_app.state.tool_registry

    assert first_registry is not second_registry
    assert (await first_registry.execute(metrics_action("first-service")))["metrics"] == {
        "error_rate": 0.11
    }
    assert (await second_registry.execute(metrics_action("second-service")))["metrics"] == {
        "error_rate": 0.22
    }


def test_tool_registry_dependency_returns_the_application_registry() -> None:
    """路由依赖必须返回应用工厂装配的同一注册表。"""
    app = create_app(scenario_store=make_store(service="catalog-service", error_rate=0.05))
    request = Request({"type": "http", "app": app})

    assert get_tool_registry(request) is app.state.tool_registry
