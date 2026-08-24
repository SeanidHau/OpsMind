"""Harness 模型与工具调用延迟观测的验收测试。"""

import asyncio
from collections import deque
from typing import Any

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    EventType,
    HarnessStatus,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation

OBSERVATION = {"status": "ok", "tool_name": "query_metrics"}


class DelayedActionProvider:
    """返回预置动作并引入可控异步延迟。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """模拟一次带耗时的模型调用。"""
        del state
        await asyncio.sleep(0.002)
        return self._actions.popleft()


class DelayedToolExecutor:
    """返回固定观察结果并引入可控异步延迟。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """模拟一次带耗时的工具调用。"""
        del action
        await asyncio.sleep(0.002)
        return OBSERVATION


def tool_action() -> AgentAction:
    """构造低风险指标查询动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name="query_metrics",
        tool_args={"service": "payment-service"},
        reason="收集诊断证据。",
    )


def final_action() -> AgentAction:
    """构造引用工具观察结果的最终回答。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已得到工具观察结果。",
        report=report_for_observation(
            tool_name="query_metrics",
            observation=OBSERVATION,
        ),
    )


def make_state() -> dict[str, Any]:
    """构造具有足够执行预算的初始状态。"""
    return create_initial_state(
        session_id="session-latency",
        thread_id="thread-latency",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


@pytest.mark.asyncio
async def test_loop_records_model_and_tool_latency() -> None:
    """模型和工具事件都必须记录非负整数毫秒耗时。"""
    loop = HarnessLoop(
        action_provider=DelayedActionProvider([tool_action(), final_action()]),
        tool_executor=DelayedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    model_events = [
        event for event in result["trajectory"] if event.event_type is EventType.MODEL_CALLED
    ]
    tool_events = [
        event for event in result["trajectory"] if event.event_type is EventType.TOOL_FINISHED
    ]

    assert len(model_events) == 2
    assert len(tool_events) == 1
    assert all(event.latency_ms is not None and event.latency_ms >= 1 for event in model_events)
    assert all(event.latency_ms is not None and event.latency_ms >= 1 for event in tool_events)
