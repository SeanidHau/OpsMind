"""Harness 强制 Replan 协议的验收测试。"""

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
    PlanItem,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation

TOOL_NAME = "query_metrics"
TOOL_OBSERVATION = {"status": "ok", "tool_name": TOOL_NAME}
REPLAN_VIOLATION = "连续停滞后必须先提交 update_plan，不能直接执行其他动作。"


class QueueActionProvider:
    """按顺序返回动作，并保留每次模型调用可见的 Replan 状态。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.replan_inputs: list[dict[str, object]] = []

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """记录协议输入；队列耗尽表示 Harness 出现了意外循环。"""
        self.replan_inputs.append(
            {
                "requested": state.get("replan_requested"),
                "reason": state.get("replan_reason"),
                "feedback": state.get("replan_feedback"),
                "correction_count": state.get("replan_correction_count"),
            }
        )
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class RepeatingToolExecutor:
    """每次返回相同观察结果，以稳定触发 Progress Verifier 停滞判定。"""

    def __init__(self) -> None:
        self.actions: list[AgentAction] = []

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """记录真正进入工具节点的动作。"""
        self.actions.append(action)
        return TOOL_OBSERVATION


def update_plan_action(title: str, reason: str) -> AgentAction:
    """构造可通过计划校验的 update_plan 动作。"""
    return AgentAction(
        action_type=ActionType.UPDATE_PLAN,
        intent="修订诊断计划",
        reason=reason,
        plan=[PlanItem(title=title, rationale="为下一轮诊断提供明确路径。")],
    )


def tool_action() -> AgentAction:
    """构造会产生重复观察的低风险工具动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务延迟",
        tool_name=TOOL_NAME,
        reason="收集指标证据。",
    )


def final_action() -> AgentAction:
    """构造引用既有工具证据的最终报告。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="证据已经足够。",
        report=report_for_observation(
            tool_name=TOOL_NAME,
            observation=TOOL_OBSERVATION,
        ),
    )


def make_state() -> dict[str, Any]:
    """构造能容纳初始计划、三次工具和一次修订的预算。"""
    return create_initial_state(
        session_id="session-replan",
        thread_id="thread-replan",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=7,
            max_tool_calls=4,
            max_model_calls=7,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


def make_loop(provider: QueueActionProvider, executor: RepeatingToolExecutor) -> HarnessLoop:
    """构造用于验证执行后停滞与 Replan 协议的 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy(
            [ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.LOW)],
            # 此夹具验证执行后的重复观察，因此允许三次相同调用进入 Verifier。
            max_identical_tool_calls=3,
        ),
    )


@pytest.mark.asyncio
async def test_harness_rejects_non_plan_action_then_accepts_corrected_replan() -> None:
    """连续停滞后，Harness 拒绝工具动作并接受下一次 update_plan。"""
    provider = QueueActionProvider(
        [
            update_plan_action("初始诊断计划", "先建立诊断步骤。"),
            tool_action(),
            tool_action(),
            tool_action(),
            # 这一动作在 Replan 期间被拒绝，不会进入工具执行器。
            tool_action(),
            update_plan_action("替代诊断计划", "重复指标没有新增信息，改为补充其他证据。"),
            final_action(),
        ]
    )
    executor = RepeatingToolExecutor()

    result = await make_loop(provider, executor).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert [action.tool_name for action in executor.actions] == [TOOL_NAME] * 3
    assert result["plan_version"] == 2
    assert result["replan_requested"] is False
    assert result["replan_reason"] is None
    assert result["replan_feedback"] is None
    assert result["replan_correction_count"] == 0
    assert EventType.PLAN_REVISED in [event.event_type for event in result["trajectory"]]

    correction_events = [
        event
        for event in result["trajectory"]
        if event.node == "replan_correction" and event.event_type is EventType.ACTION_BLOCKED
    ]
    assert len(correction_events) == 1
    assert correction_events[0].decision == REPLAN_VIOLATION
    assert provider.replan_inputs[4] == {
        "requested": True,
        "reason": "重复动作返回了与历史相同的观察结果。",
        "feedback": None,
        "correction_count": 0,
    }
    assert provider.replan_inputs[5]["feedback"] == REPLAN_VIOLATION
    assert provider.replan_inputs[5]["correction_count"] == 1


@pytest.mark.asyncio
async def test_harness_blocks_after_replan_correction_limit() -> None:
    """模型第二次忽略 Replan 协议时，Harness 必须停止运行。"""
    provider = QueueActionProvider(
        [
            update_plan_action("初始诊断计划", "先建立诊断步骤。"),
            tool_action(),
            tool_action(),
            tool_action(),
            tool_action(),
            tool_action(),
        ]
    )
    executor = RepeatingToolExecutor()

    result = await make_loop(provider, executor).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert [action.tool_name for action in executor.actions] == [TOOL_NAME] * 3
    assert result["errors"][-1] == "模型未能在规定次数内提交重新规划。"
    assert result["replan_correction_count"] == 2
    assert result["replan_feedback"] == REPLAN_VIOLATION
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


def test_harness_rejects_negative_replan_correction_limit() -> None:
    """纠正次数上限必须在 Harness 初始化时校验。"""
    with pytest.raises(ValueError, match="max_replan_corrections"):
        HarnessLoop(
            action_provider=QueueActionProvider([]),
            tool_executor=RepeatingToolExecutor(),
            policy=ActionPolicy([]),
            max_replan_corrections=-1,
        )
