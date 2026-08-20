"""ToolRegistry 的验收测试。"""

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
    ToolDefinition,
    ToolRiskLevel,
)
from app.tools.registry import ToolExecutionError, ToolRegistry


class QueueActionProvider:
    """按固定顺序返回动作，避免测试依赖模型输出。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一动作；空队列表示图产生了意外循环。"""
        del state
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


def metrics_definition() -> ToolDefinition:
    """构造具有显式参数约束的只读指标工具定义。"""
    return ToolDefinition(
        name="query_metrics",
        description="查询服务错误率和延迟指标。",
        risk_level=ToolRiskLevel.LOW,
        required_args=("service",),
        allowed_args=("service", "window_minutes"),
    )


def tool_action(**tool_args: Any) -> AgentAction:
    """构造调用指标工具的模型动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name="query_metrics",
        tool_args=tool_args,
        reason="收集错误率证据",
    )


@pytest.mark.asyncio
async def test_registry_executes_registered_tool_and_exposes_policy() -> None:
    """注册表应将工具定义投影为 Policy 可使用的工具策略。"""
    received_args: list[dict[str, Any]] = []

    async def query_metrics(args: dict[str, Any]) -> dict[str, Any]:
        """记录参数并返回固定的可审计结果。"""
        received_args.append(args)
        return {"error_rate": 0.12}

    registry = ToolRegistry()
    registry.register(metrics_definition(), query_metrics)

    result = await registry.execute(tool_action(service="payment"))

    assert result == {"error_rate": 0.12}
    assert received_args == [{"service": "payment"}]
    assert registry.policies()[0].name == "query_metrics"
    assert registry.policies()[0].risk_level is ToolRiskLevel.LOW


@pytest.mark.asyncio
async def test_registry_rejects_missing_or_unexpected_arguments() -> None:
    """处理函数不能收到缺失必填字段或未声明字段的调用。"""
    called = False

    async def query_metrics(args: dict[str, Any]) -> dict[str, Any]:
        """若参数校验失效，本函数会暴露错误执行。"""
        nonlocal called
        called = True
        return args

    registry = ToolRegistry()
    registry.register(metrics_definition(), query_metrics)

    with pytest.raises(ToolExecutionError, match="missing required args: service"):
        await registry.execute(tool_action())

    with pytest.raises(ToolExecutionError, match="unexpected args: region"):
        await registry.execute(tool_action(service="payment", region="cn"))

    assert called is False


def test_registry_rejects_duplicate_tool_name() -> None:
    """同名工具会产生不确定路由，因此启动时必须拒绝。"""
    registry = ToolRegistry()

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        """提供满足注册接口的空处理函数。"""
        return args

    registry.register(metrics_definition(), handler)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(metrics_definition(), handler)


@pytest.mark.asyncio
async def test_registry_executes_through_harness_loop() -> None:
    """注册表可直接作为 Loop 的 ToolExecutor，不能绕过 Policy。"""

    async def query_metrics(args: dict[str, Any]) -> dict[str, Any]:
        """返回与本次服务相关的模拟指标。"""
        return {"service": args["service"], "error_rate": 0.12}

    registry = ToolRegistry()
    registry.register(metrics_definition(), query_metrics)
    loop = HarnessLoop(
        action_provider=QueueActionProvider(
            [
                tool_action(service="payment"),
                AgentAction(
                    action_type=ActionType.FINAL_ANSWER,
                    intent="输出诊断结果",
                    reason="指标已收集",
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
    assert result["tool_results"][0]["result"] == {
        "service": "payment",
        "error_rate": 0.12,
    }
