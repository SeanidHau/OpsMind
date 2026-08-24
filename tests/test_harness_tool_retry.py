"""HarnessLoop 工具重试的验收测试。"""

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
    """按固定顺序提供动作，确保重试不会额外调用模型。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.calls = 0

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一动作；队列耗尽说明 Loop 发生了意外重规划。"""
        del state
        self.calls += 1
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class FlakyToolExecutor:
    """在指定次数内失败，用于模拟瞬态工具故障。"""

    def __init__(self, failures_before_success: int) -> None:
        self._failures_before_success = failures_before_success
        self.attempts = 0

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """先抛出预设错误，之后返回成功观察结果。"""
        self.attempts += 1
        if self.attempts <= self._failures_before_success:
            raise ConnectionError(f"temporary failure {self.attempts}")
        return {"status": "ok", "tool_name": action.tool_name}


def tool_action() -> AgentAction:
    """构造需要重试的低风险工具动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name="query_metrics",
        tool_args={"service": "payment-service"},
        reason="收集诊断证据",
    )


def final_action() -> AgentAction:
    """构造成功收集证据后的终止动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已经收集到足够证据",
        report=report_for_observation(
            tool_name="query_metrics",
            observation={"status": "ok", "tool_name": "query_metrics"},
        ),
    )


def make_state(*, max_tool_calls: int = 3) -> dict[str, Any]:
    """构造仅调整工具预算的初始状态。"""
    return create_initial_state(
        session_id="session-retry",
        thread_id="thread-retry",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=max_tool_calls,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


def make_loop(
    provider: QueueActionProvider,
    executor: FlakyToolExecutor,
    *,
    max_tool_retries: int,
) -> HarnessLoop:
    """构造注册了低风险只读工具的 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
        max_tool_retries=max_tool_retries,
    )


@pytest.mark.asyncio
async def test_loop_retries_transient_failure_without_reproposing_action() -> None:
    """首次失败后应直接重试同一工具，不额外调用 Action Provider。"""
    provider = QueueActionProvider([tool_action(), final_action()])
    executor = FlakyToolExecutor(failures_before_success=1)

    result = await make_loop(provider, executor, max_tool_retries=1).run(make_state())

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert executor.attempts == 2
    assert provider.calls == 2
    assert result["retry_count"] == 0
    assert result["tool_call_count"] == 2
    assert result["budget"].used_tool_calls == 2
    assert EventType.TOOL_RETRY in [event.event_type for event in result["trajectory"]]


@pytest.mark.asyncio
async def test_loop_fails_after_retry_limit_is_exhausted() -> None:
    """连续失败超过重试上限后，Harness 必须终止而不是无限重试。"""
    provider = QueueActionProvider([tool_action()])
    executor = FlakyToolExecutor(failures_before_success=10)

    result = await make_loop(provider, executor, max_tool_retries=1).run(make_state())

    assert result["terminal_status"] is HarnessStatus.FAILED
    assert executor.attempts == 2
    assert provider.calls == 1
    assert result["retry_count"] == 2
    assert result["tool_call_count"] == 2
    assert result["trajectory"][-2].event_type is EventType.RUN_FAILED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_loop_blocks_retry_that_exceeds_tool_budget() -> None:
    """重试前必须检查剩余工具预算，不能绕过 Policy 的预算约束。"""
    provider = QueueActionProvider([tool_action()])
    executor = FlakyToolExecutor(failures_before_success=1)

    result = await make_loop(provider, executor, max_tool_retries=1).run(
        make_state(max_tool_calls=1)
    )

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert executor.attempts == 1
    assert result["budget"].used_tool_calls == 1
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


def test_loop_rejects_negative_retry_limit() -> None:
    """重试次数可以为零，但不能为负数。"""
    with pytest.raises(ValueError, match="max_tool_retries"):
        make_loop(
            QueueActionProvider([tool_action()]),
            FlakyToolExecutor(failures_before_success=0),
            max_tool_retries=-1,
        )
