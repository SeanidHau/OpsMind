"""HarnessLoop 模型调用重试的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.evidence import EvidenceCollector
from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    EventType,
    HarnessStatus,
)
from tests.support import diagnosis_report

OBSERVATION = {"status": "ok", "tool_name": "query_metrics"}


class OutcomeActionProvider:
    """按顺序返回动作或抛出临时模型异常。"""

    def __init__(self, outcomes: list[AgentAction | Exception]) -> None:
        self._outcomes = deque(outcomes)
        self.calls = 0

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """模拟可恢复与不可恢复的模型调用结果。"""
        del state
        self.calls += 1
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class UnusedToolExecutor:
    """确保本阶段模型失败路径不会意外执行工具。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """任何工具执行都表示模型重试路由错误。"""
        del action
        raise AssertionError("model retry tests must not execute tools")


def final_action() -> AgentAction:
    """构造可通过证据门槛的最终回答。"""
    evidence = EvidenceCollector().collect(
        tool_name="query_metrics",
        observation=OBSERVATION,
    )
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已收集到可引用的诊断证据。",
        report=diagnosis_report(evidence_ids=[evidence.evidence_id]),
    )


def make_state(*, max_model_calls: int = 3) -> dict[str, Any]:
    """构造预置证据且可调整模型调用预算的初始状态。"""
    state = create_initial_state(
        session_id="session-model-retry",
        thread_id="thread-model-retry",
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
    state["evidence"] = [
        EvidenceCollector().collect(
            tool_name="query_metrics",
            observation=OBSERVATION,
        )
    ]
    return state


def make_loop(
    provider: OutcomeActionProvider,
    *,
    max_model_retries: int = 1,
) -> HarnessLoop:
    """构造零等待的模型重试 Harness，保证测试快速且稳定。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=UnusedToolExecutor(),
        policy=ActionPolicy([]),
        max_model_retries=max_model_retries,
        model_retry_delay_seconds=0,
    )


@pytest.mark.asyncio
async def test_loop_retries_transient_model_failure_and_completes() -> None:
    """瞬时模型错误后应重试，并继续执行返回的合法动作。"""
    provider = OutcomeActionProvider([RuntimeError("temporary model error"), final_action()])

    result = await make_loop(provider).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.calls == 2
    assert result["budget"].used_model_calls == 2
    assert [event.event_type for event in result["trajectory"]].count(EventType.MODEL_CALLED) == 2
    assert [event.event_type for event in result["trajectory"]].count(EventType.MODEL_RETRY) == 1
    assert [event.event_type for event in result["trajectory"]].count(
        EventType.ACTION_PROPOSED
    ) == 1


@pytest.mark.asyncio
async def test_loop_fails_and_archives_when_model_retries_are_exhausted() -> None:
    """模型连续失败超过重试次数后必须归档为 FAILED。"""
    provider = OutcomeActionProvider(
        [RuntimeError("first failure"), RuntimeError("second failure")]
    )

    result = await make_loop(provider).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.FAILED
    assert provider.calls == 2
    assert result["budget"].used_model_calls == 2
    assert result["errors"][-2:] == ["first failure", "second failure"]
    assert result["trajectory"][-2].event_type is EventType.RUN_FAILED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_loop_blocks_model_retry_that_exceeds_model_budget() -> None:
    """模型重试前必须检查模型调用预算，不能绕过预算上限。"""
    provider = OutcomeActionProvider([RuntimeError("temporary model error")])

    result = await make_loop(provider).run(
        make_state(max_model_calls=1)  # type: ignore[arg-type]
    )

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert provider.calls == 1
    assert result["budget"].used_model_calls == 1
    assert result["errors"][-2:] == [
        "temporary model error",
        "调用模型会超出本次运行预算。",
    ]
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


def test_loop_rejects_negative_model_retry_configuration() -> None:
    """模型重试次数和退避时间不能为负数。"""
    provider = OutcomeActionProvider([final_action()])

    with pytest.raises(ValueError, match="max_model_retries"):
        HarnessLoop(
            action_provider=provider,
            tool_executor=UnusedToolExecutor(),
            policy=ActionPolicy([]),
            max_model_retries=-1,
        )

    with pytest.raises(ValueError, match="model_retry_delay_seconds"):
        HarnessLoop(
            action_provider=provider,
            tool_executor=UnusedToolExecutor(),
            policy=ActionPolicy([]),
            model_retry_delay_seconds=-0.1,
        )
