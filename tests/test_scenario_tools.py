"""预设故障场景工具的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    HarnessStatus,
    IncidentScenario,
    ScenarioLog,
)
from app.tools.registry import ToolExecutionError, ToolRegistry
from app.tools.scenarios import ScenarioStore, register_scenario_tools
from tests.support import report_for_observation


class QueueActionProvider:
    """按固定顺序返回动作，验证工具层而非模型输出。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一个预设动作。"""
        del state
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


def payment_timeout_scenario() -> IncidentScenario:
    """构造数据库连接池耗尽导致超时的固定场景。"""
    return IncidentScenario(
        scenario_id="payment_timeout_001",
        service="payment-service",
        logs=[
            ScenarioLog(
                timestamp="2026-08-14T10:01:00Z",
                level="ERROR",
                message="database connection pool exhausted",
            ),
            ScenarioLog(
                timestamp="2026-08-14T10:02:00Z",
                level="WARN",
                message="payment request latency increased",
            ),
        ],
        metrics={"error_rate": 0.12, "p95_latency_ms": 2_500.0},
        dependencies=["order-service", "postgres-primary"],
    )


def tool_action(name: str, **tool_args: Any) -> AgentAction:
    """构造调用场景工具的模型动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent=f"调用 {name}",
        tool_name=name,
        tool_args=tool_args,
        reason="收集诊断证据",
    )


@pytest.mark.asyncio
async def test_query_logs_filters_fixed_scenario_data() -> None:
    """日志工具应仅返回服务匹配且包含关键词的预设日志。"""
    registry = ToolRegistry()
    register_scenario_tools(registry, ScenarioStore([payment_timeout_scenario()]))

    result = await registry.execute(
        tool_action(
            "query_logs",
            service="payment-service",
            contains="pool",
        )
    )

    assert result["service"] == "payment-service"
    assert result["count"] == 1
    assert result["logs"][0]["message"] == "database connection pool exhausted"


@pytest.mark.asyncio
async def test_tools_return_metrics_and_topology_for_same_scenario() -> None:
    """不同工具从同一固定场景读取指标和服务依赖。"""
    registry = ToolRegistry()
    register_scenario_tools(registry, ScenarioStore([payment_timeout_scenario()]))

    metrics = await registry.execute(tool_action("query_metrics", service="payment-service"))
    topology = await registry.execute(tool_action("query_topology", service="payment-service"))

    assert metrics["metrics"]["error_rate"] == 0.12
    assert topology["dependencies"] == ["order-service", "postgres-primary"]


@pytest.mark.asyncio
async def test_tools_accept_an_unambiguous_service_shorthand() -> None:
    """模型省略稳定 `-service` 后缀时仍应命中同一受控场景。"""
    registry = ToolRegistry()
    register_scenario_tools(registry, ScenarioStore([payment_timeout_scenario()]))

    metrics = await registry.execute(tool_action("query_metrics", service="payment"))

    assert metrics["service"] == "payment-service"


@pytest.mark.asyncio
async def test_unknown_service_is_reported_as_tool_execution_error() -> None:
    """不存在的服务不能伪造空证据，必须显式返回工具错误。"""
    registry = ToolRegistry()
    register_scenario_tools(registry, ScenarioStore([payment_timeout_scenario()]))

    with pytest.raises(ToolExecutionError, match="scenario not found: unknown-service"):
        await registry.execute(tool_action("query_metrics", service="unknown-service"))


@pytest.mark.asyncio
async def test_scenario_tool_executes_through_harness_loop() -> None:
    """场景工具可经 Policy、Registry 和 Loop 写入统一观察结果。"""
    registry = ToolRegistry()
    register_scenario_tools(registry, ScenarioStore([payment_timeout_scenario()]))
    loop = HarnessLoop(
        action_provider=QueueActionProvider(
            [
                tool_action("query_metrics", service="payment-service"),
                AgentAction(
                    action_type=ActionType.FINAL_ANSWER,
                    intent="输出诊断结论",
                    reason="已收集核心指标",
                    report=report_for_observation(
                        tool_name="query_metrics",
                        observation={
                            "service": "payment-service",
                            "metrics": {"error_rate": 0.12, "p95_latency_ms": 2_500.0},
                        },
                    ),
                ),
            ]
        ),
        tool_executor=registry,
        policy=ActionPolicy(registry.policies()),
    )
    state = create_initial_state(
        session_id="session-1",
        thread_id="thread-1",
        user_query="支付服务超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )

    result = await loop.run(state)

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["tool_results"][0]["result"]["metrics"]["p95_latency_ms"] == 2_500.0
