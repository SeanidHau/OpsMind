"""模型失败分类策略的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.evidence import EvidenceCollector
from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.model_failure import (
    DefaultModelFailureClassifier,
    ModelFailure,
)
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
    """按顺序返回动作或抛出指定模型异常。"""

    def __init__(self, outcomes: list[AgentAction | Exception]) -> None:
        self._outcomes = deque(outcomes)
        self.calls = 0

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """模拟模型提供器的成功与失败结果。"""
        del state
        self.calls += 1
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class UnusedToolExecutor:
    """保证模型失败测试不进入工具执行路径。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """工具执行表示模型失败路由错误。"""
        del action
        raise AssertionError("model failure tests must not execute tools")


class RetryRuntimeErrorClassifier:
    """用于验证 Harness 可注入自定义异常分类策略。"""

    def classify(self, error: Exception) -> ModelFailure:
        """将测试中的 RuntimeError 显式标记为可重试。"""
        return ModelFailure(
            retryable=isinstance(error, RuntimeError),
            category="custom_runtime_error",
            message=str(error),
        )


def final_action() -> AgentAction:
    """构造引用预置证据的最终回答。"""
    evidence = EvidenceCollector().collect(
        tool_name="query_metrics",
        observation=OBSERVATION,
    )
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已具备可追溯证据。",
        report=diagnosis_report(evidence_ids=[evidence.evidence_id]),
    )


def make_state() -> dict[str, Any]:
    """构造预置证据的最小 Harness 状态。"""
    state = create_initial_state(
        session_id="session-model-failure",
        thread_id="thread-model-failure",
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
    classifier: RetryRuntimeErrorClassifier | None = None,
) -> HarnessLoop:
    """构造零退避的模型失败测试 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=UnusedToolExecutor(),
        policy=ActionPolicy([]),
        max_model_retries=1,
        model_retry_delay_seconds=0,
        model_failure_classifier=classifier,
    )


@pytest.mark.parametrize(
    ("error", "retryable", "category"),
    [
        (ConnectionError("network down"), True, "transient_transport_error"),
        (PermissionError("forbidden"), False, "authorization_error"),
        (
            ValueError("structured action response did not contain a parsed action"),
            True,
            "empty_structured_output",
        ),
        (ValueError("invalid output"), False, "invalid_model_response"),
        (RuntimeError("unknown"), False, "unclassified_model_error"),
    ],
)
def test_default_classifier_uses_explicit_failure_categories(
    error: Exception,
    retryable: bool,
    category: str,
) -> None:
    """默认分类器只重试明确的传输故障。"""
    failure = DefaultModelFailureClassifier().classify(error)

    assert failure.retryable is retryable
    assert failure.category == category


@pytest.mark.asyncio
async def test_loop_fails_without_retry_for_invalid_model_response() -> None:
    """结构化输出错误必须立即失败，不能消耗额外模型调用。"""
    provider = OutcomeActionProvider([ValueError("invalid structured output")])

    result = await make_loop(provider).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.FAILED
    assert provider.calls == 1
    assert result["budget"].used_model_calls == 1
    assert EventType.MODEL_RETRY not in [event.event_type for event in result["trajectory"]]
    assert result["trajectory"][-2].observation == {
        "category": "invalid_model_response",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_loop_accepts_injected_failure_classifier() -> None:
    """调用方可将特定提供器异常显式定义为可重试。"""
    provider = OutcomeActionProvider([RuntimeError("provider busy"), final_action()])

    result = await make_loop(
        provider,
        classifier=RetryRuntimeErrorClassifier(),
    ).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.calls == 2
    retry_event = next(
        event for event in result["trajectory"] if event.event_type is EventType.MODEL_RETRY
    )
    assert retry_event.observation == {
        "category": "custom_runtime_error",
        "retryable": True,
    }
