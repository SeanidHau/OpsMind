"""HarnessLoop 最终回答证据门槛的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.evidence import EvidenceGate
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
    """按固定顺序返回动作，隔离模型输出的不确定性。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一个动作。"""
        del state
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class FixedToolExecutor:
    """返回一条固定观察结果，用于生成单条证据。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """返回与动作关联的结构化结果。"""
        return {"service": action.tool_args["service"], "error_rate": 0.12}


def tool_action() -> AgentAction:
    """构造指标查询动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务错误率",
        tool_name="query_metrics",
        tool_args={"service": "payment-service"},
        reason="收集诊断证据",
    )


def final_action() -> AgentAction:
    """构造最终回答动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="准备结束诊断",
        report=report_for_observation(
            tool_name="query_metrics",
            observation={"service": "payment-service", "error_rate": 0.12},
        ),
    )


def make_state() -> dict[str, Any]:
    """构造允许完成两轮动作的初始状态。"""
    return create_initial_state(
        session_id="session-evidence-gate",
        thread_id="thread-evidence-gate",
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


def make_loop(
    actions: list[AgentAction],
    *,
    evidence_gate: EvidenceGate | None = None,
) -> HarnessLoop:
    """构造带低风险指标工具和可选证据门槛的 Harness。"""
    return HarnessLoop(
        action_provider=QueueActionProvider(actions),
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
        evidence_gate=evidence_gate,
    )


@pytest.mark.asyncio
async def test_final_answer_without_evidence_is_blocked() -> None:
    """没有工具观察产生的证据时，最终回答必须被拒绝。"""
    result = await make_loop([final_action()]).run(make_state())

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["errors"][-1] == "final answer requires at least 1 evidence items"
    assert result["trajectory"][-1].event_type is EventType.VERIFICATION_FAILED


@pytest.mark.asyncio
async def test_final_answer_with_evidence_is_completed() -> None:
    """至少一条结构化证据满足默认门槛后，最终回答可完成。"""
    result = await make_loop([tool_action(), final_action()]).run(make_state())

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert len(result["evidence"]) == 1


@pytest.mark.asyncio
async def test_custom_evidence_threshold_blocks_insufficient_evidence() -> None:
    """自定义门槛高于当前证据数量时，Harness 必须继续阻断完成。"""
    result = await make_loop(
        [tool_action(), final_action()],
        evidence_gate=EvidenceGate(min_evidence=2),
    ).run(make_state())

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["errors"][-1] == "final answer requires at least 2 evidence items"


def test_evidence_gate_rejects_non_positive_threshold() -> None:
    """证据门槛必须至少为一条。"""
    with pytest.raises(ValueError, match="min_evidence"):
        EvidenceGate(min_evidence=0)
