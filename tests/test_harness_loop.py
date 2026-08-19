"""LangGraph Harness Loop 的验收测试。"""

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


class QueueActionProvider:
    """按既定顺序返回动作，替代真实模型以保持测试可重复。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一个动作；队列耗尽代表 Loop 意外多执行了一轮。"""
        del state
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class RecordingToolExecutor:
    """记录实际调用的工具动作，并返回固定观察结果。"""

    def __init__(self) -> None:
        self.actions: list[AgentAction] = []

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """记录执行动作，供断言策略层是否拦截调用。"""
        self.actions.append(action)
        return {"status": "ok", "tool_name": action.tool_name}


def make_budget(*, max_steps: int = 5) -> BudgetState:
    """构造只关注步骤和工具调用次数的测试预算。"""
    return BudgetState(
        max_steps=max_steps,
        max_tool_calls=3,
        max_model_calls=3,
        max_tokens=1_000,
        max_runtime_seconds=60,
        max_estimated_cost_usd=1.0,
    )


def tool_action(name: str) -> AgentAction:
    """构造一个由 Loop 处理的工具调用动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent=f"调用 {name}",
        tool_name=name,
        reason="收集故障诊断证据",
    )


def final_action() -> AgentAction:
    """构造正常结束 Loop 的最终回答动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出当前诊断结论",
        reason="已经收集到足够证据",
    )


def make_state(*, budget: BudgetState) -> dict[str, Any]:
    """创建具有全部默认领域字段的初始图状态。"""
    return create_initial_state(
        session_id="session-1",
        thread_id="thread-1",
        user_query="支付服务请求超时",
        budget=budget,
    )


@pytest.mark.asyncio
async def test_loop_executes_allowed_tool_then_completes() -> None:
    """低风险工具获准后执行，Loop 随后的最终回答应终止图。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics"), final_action()]),
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert [action.tool_name for action in executor.actions] == ["query_metrics"]
    assert result["budget"].used_steps == 2
    assert result["budget"].used_tool_calls == 1
    assert result["tool_results"] == [
        {"tool_name": "query_metrics", "result": {"status": "ok", "tool_name": "query_metrics"}}
    ]
    assert EventType.ACTION_PROPOSED in [event.event_type for event in result["trajectory"]]
    assert EventType.TOOL_FINISHED in [event.event_type for event in result["trajectory"]]
    assert result["trajectory"][-1].event_type is EventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_loop_pauses_for_high_risk_tool_without_executing_it() -> None:
    """高风险工具转入待审批状态，不能消费预算或调用执行器。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("generate_restart_plan")]),
        tool_executor=executor,
        policy=ActionPolicy(
            [
                ToolPolicy(
                    name="generate_restart_plan",
                    risk_level=ToolRiskLevel.HIGH,
                )
            ]
        ),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.WAITING_APPROVAL
    assert result["approval_request"] == {
        "tool_name": "generate_restart_plan",
        "reason": "该工具的风险策略要求人工审批。",
    }
    assert executor.actions == []
    assert result["budget"].used_steps == 0
    assert result["trajectory"][-1].event_type is EventType.RUN_PAUSED


@pytest.mark.asyncio
async def test_loop_blocks_unknown_tool_before_execution() -> None:
    """未注册工具必须留下拒绝事件，且执行器完全不可见该动作。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("unknown_tool")]),
        tool_executor=executor,
        policy=ActionPolicy([]),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert executor.actions == []
    assert result["errors"] == ["请求的工具未在当前运行中注册。"]
    assert result["trajectory"][-1].event_type is EventType.ACTION_BLOCKED


@pytest.mark.asyncio
async def test_loop_stops_when_next_action_exceeds_budget() -> None:
    """已消费完步骤预算后，下一轮动作必须被阻断而不是无限循环。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics"), final_action()]),
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state(budget=make_budget(max_steps=1)))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert [action.tool_name for action in executor.actions] == ["query_metrics"]
    assert result["budget"].used_steps == 1
    assert result["trajectory"][-1].event_type is EventType.ACTION_BLOCKED
