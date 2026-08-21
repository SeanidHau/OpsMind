"""HarnessLoop 模型调用预算的验收测试。"""

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


class QueueActionProvider:
    """记录模型调用次数，并按固定顺序返回动作。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.calls = 0

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """模拟一次模型调用。"""
        del state
        self.calls += 1
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class FixedToolExecutor:
    """返回固定观察结果的低风险工具执行器。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """返回工具名称，供 Loop 继续进入下一次模型调用。"""
        return {"status": "ok", "tool_name": action.tool_name}


def tool_action() -> AgentAction:
    """构造一次低风险指标查询。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询指标",
        tool_name="query_metrics",
        reason="收集故障证据",
    )


def final_action() -> AgentAction:
    """构造正常结束动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出结论",
        reason="证据已收集完成",
        report=report_for_observation(
            tool_name="query_metrics",
            observation={"status": "ok", "tool_name": "query_metrics"},
        ),
    )


def make_state(*, max_model_calls: int) -> dict[str, Any]:
    """构造只调整模型调用上限的初始状态。"""
    return create_initial_state(
        session_id="session-model-budget",
        thread_id="thread-model-budget",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=max_model_calls,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


def make_loop(provider: QueueActionProvider) -> HarnessLoop:
    """构造带固定工具策略的 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )


@pytest.mark.asyncio
async def test_loop_consumes_model_budget_and_records_model_events() -> None:
    """每次调用 Action Provider 都应消费模型预算并写入轨迹。"""
    provider = QueueActionProvider([tool_action(), final_action()])

    result = await make_loop(provider).run(make_state(max_model_calls=2))

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.calls == 2
    assert result["budget"].used_model_calls == 2
    assert [event.event_type for event in result["trajectory"]].count(EventType.MODEL_CALLED) == 2


@pytest.mark.asyncio
async def test_loop_blocks_before_calling_model_after_budget_is_exhausted() -> None:
    """下一轮模型预算不足时，不能调用 Provider 或产生候选动作。"""
    provider = QueueActionProvider([tool_action(), final_action()])

    result = await make_loop(provider).run(make_state(max_model_calls=1))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert provider.calls == 1
    assert result["budget"].used_model_calls == 1
    assert result["tool_call_count"] == 1
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


def test_budget_contract_rejects_zero_model_budget() -> None:
    """模型预算上限必须为正数，零值在运行启动前就应被拒绝。"""
    with pytest.raises(ValueError, match="max_model_calls"):
        BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=0,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        )
