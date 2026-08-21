"""Harness 最终报告接入的验收测试。"""

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
from tests.support import diagnosis_report, report_for_observation


class QueueActionProvider:
    """按顺序提供预设动作，隔离模型输出的不确定性。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一动作。"""
        del state
        return self._actions.popleft()


class FixedToolExecutor:
    """返回稳定观察结果，使证据 ID 可在报告中预先引用。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """返回固定指标结果。"""
        del action
        return {"service": "payment-service", "error_rate": 0.12}


def make_state() -> dict[str, Any]:
    """构造可执行一次工具调用和一次最终回答的状态。"""
    return create_initial_state(
        session_id="session-final-report",
        thread_id="thread-final-report",
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


def tool_action() -> AgentAction:
    """构造生成诊断证据的低风险工具调用。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务错误率",
        tool_name="query_metrics",
        reason="收集诊断证据",
    )


def make_loop(actions: list[AgentAction]) -> HarnessLoop:
    """构造使用固定指标工具的 Harness。"""
    return HarnessLoop(
        action_provider=QueueActionProvider(actions),
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )


@pytest.mark.asyncio
async def test_final_answer_persists_validated_report_and_markdown() -> None:
    """满足证据门槛且引用存在时，Harness 应保存两种报告表示。"""
    report = report_for_observation(
        tool_name="query_metrics",
        observation={"service": "payment-service", "error_rate": 0.12},
    )

    result = await make_loop(
        [
            tool_action(),
            AgentAction(
                action_type=ActionType.FINAL_ANSWER,
                intent="输出诊断结论",
                reason="指标证据已收集。",
                report=report,
            ),
        ]
    ).run(make_state())

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["diagnosis_report"] == report
    assert result["diagnosis"] == report.model_dump(mode="json")
    assert "# OpsMind 诊断报告" in result["final_answer"]


@pytest.mark.asyncio
async def test_final_answer_with_unknown_report_reference_is_blocked() -> None:
    """证据门槛满足后，未知引用仍必须阻断最终回答。"""
    result = await make_loop(
        [
            tool_action(),
            AgentAction(
                action_type=ActionType.FINAL_ANSWER,
                intent="输出诊断结论",
                reason="指标证据已收集。",
                report=diagnosis_report(evidence_ids=["b" * 64]),
            ),
        ]
    ).run(make_state())

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["errors"][-1] == f"unknown evidence references: {'b' * 64}"
    assert result["trajectory"][-1].event_type is EventType.VERIFICATION_FAILED
