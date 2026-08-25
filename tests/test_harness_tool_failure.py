"""工具失败分类策略的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.tool_failure import (
    DefaultToolFailureClassifier,
    ToolFailure,
)
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    EventType,
    HarnessStatus,
    PlanItem,
    ToolPolicy,
    ToolRiskLevel,
)
from app.tools.registry import ToolExecutionError
from tests.support import report_for_observation

OBSERVATION = {"status": "ok", "tool_name": "query_metrics"}


class QueueActionProvider:
    """按顺序提供动作，确保工具失败不会重复调用模型。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.calls = 0
        self.replan_inputs: list[dict[str, object]] = []

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一条预置动作。"""
        self.replan_inputs.append(
            {
                "requested": state.get("replan_requested"),
                "reason": state.get("replan_reason"),
                "feedback": state.get("replan_feedback"),
            }
        )
        self.calls += 1
        return self._actions.popleft()


class OutcomeToolExecutor:
    """按顺序返回工具观察结果或抛出指定异常。"""

    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self._outcomes = deque(outcomes)
        self.calls = 0

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """模拟工具提供器的成功与失败结果。"""
        del action
        self.calls += 1
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RetryRuntimeErrorClassifier:
    """用于验证可按工具供应商异常扩展分类策略。"""

    def classify(self, error: Exception) -> ToolFailure:
        """将测试中的 RuntimeError 显式定义为临时故障。"""
        return ToolFailure(
            retryable=isinstance(error, RuntimeError),
            category="custom_runtime_error",
            message=str(error),
        )


def tool_action(tool_name: str = "query_metrics") -> AgentAction:
    """构造低风险只读工具动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name=tool_name,
        tool_args={"service": "payment-service"},
        reason="收集诊断证据。",
    )


def final_action(
    *,
    tool_name: str = "query_metrics",
    observation: dict[str, Any] = OBSERVATION,
) -> AgentAction:
    """构造引用确定性工具观察结果的最终回答。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已获得可引用的工具观察结果。",
        report=report_for_observation(
            tool_name=tool_name,
            observation=observation,
        ),
    )


def update_plan_action() -> AgentAction:
    """构造重试耗尽后必须提交的替代诊断计划。"""
    return AgentAction(
        action_type=ActionType.UPDATE_PLAN,
        intent="修订诊断计划",
        reason="指标查询连续失败，改用日志查询收集证据。",
        plan=[PlanItem(title="查询支付服务日志", rationale="替代不可用的指标查询路径。")],
    )


def make_state(*, max_model_calls: int = 3) -> dict[str, Any]:
    """构造具有足够模型与工具预算的初始状态。"""
    return create_initial_state(
        session_id="session-tool-failure",
        thread_id="thread-tool-failure",
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


def make_loop(
    provider: QueueActionProvider,
    executor: OutcomeToolExecutor,
    *,
    classifier: RetryRuntimeErrorClassifier | None = None,
) -> HarnessLoop:
    """构造零等待、低风险工具的 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
        max_tool_retries=1,
        tool_failure_classifier=classifier,
    )


@pytest.mark.parametrize(
    ("error", "retryable", "category"),
    [
        (ConnectionError("network down"), True, "transient_transport_error"),
        (PermissionError("forbidden"), False, "authorization_error"),
        (ToolExecutionError("missing required args"), False, "invalid_tool_request"),
        (RuntimeError("unknown"), False, "unclassified_tool_error"),
    ],
)
def test_default_classifier_uses_explicit_failure_categories(
    error: Exception,
    retryable: bool,
    category: str,
) -> None:
    """默认策略只重试明确的传输类工具故障。"""
    failure = DefaultToolFailureClassifier().classify(error)

    assert failure.retryable is retryable
    assert failure.category == category


@pytest.mark.asyncio
async def test_loop_fails_without_retry_for_invalid_tool_request() -> None:
    """工具参数或注册错误必须立即失败，不得继续重试。"""
    provider = QueueActionProvider([tool_action()])
    executor = OutcomeToolExecutor([ToolExecutionError("missing required args: service")])

    result = await make_loop(provider, executor).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.FAILED
    assert provider.calls == 1
    assert executor.calls == 1
    assert result["budget"].used_tool_calls == 1
    assert EventType.TOOL_RETRY not in [event.event_type for event in result["trajectory"]]
    assert result["trajectory"][-2].observation == {
        "category": "invalid_tool_request",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_loop_accepts_injected_tool_failure_classifier() -> None:
    """调用方可为工具供应商异常注入专用重试分类。"""
    provider = QueueActionProvider([tool_action(), final_action()])
    executor = OutcomeToolExecutor([RuntimeError("provider busy"), OBSERVATION])

    result = await make_loop(
        provider,
        executor,
        classifier=RetryRuntimeErrorClassifier(),
    ).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.calls == 2
    assert executor.calls == 2
    retry_event = next(
        event for event in result["trajectory"] if event.event_type is EventType.TOOL_RETRY
    )
    assert retry_event.observation == {
        "category": "custom_runtime_error",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_loop_replans_after_transient_failure_retries_are_exhausted() -> None:
    """启用降级后，临时故障必须先重规划，再执行替代工具。"""
    fallback_observation = {"status": "ok", "tool_name": "query_logs"}
    provider = QueueActionProvider(
        [
            tool_action(),
            update_plan_action(),
            tool_action("query_logs"),
            final_action(tool_name="query_logs", observation=fallback_observation),
        ]
    )
    executor = OutcomeToolExecutor(
        [
            ConnectionError("network down"),
            ConnectionError("network down"),
            fallback_observation,
        ]
    )
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy(
            [
                ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW),
                ToolPolicy(name="query_logs", risk_level=ToolRiskLevel.LOW),
            ]
        ),
        max_tool_retries=1,
        replan_on_tool_failure=True,
    )

    result = await loop.run(make_state(max_model_calls=4))  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert executor.calls == 3
    assert provider.calls == 4
    assert result["tool_call_count"] == 3
    assert result["replan_requested"] is False
    assert provider.replan_inputs[1] == {
        "requested": True,
        "reason": "工具调用在重试耗尽后失败，需要重新规划替代诊断路径。",
        "feedback": "network down",
    }
    fallback_event = next(
        event
        for event in result["trajectory"]
        if event.event_type is EventType.VERIFICATION_FAILED and event.node == "execute_tool"
    )
    assert fallback_event.observation == {
        "category": "transient_transport_error",
        "retryable": True,
    }
