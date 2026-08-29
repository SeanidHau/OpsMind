"""Harness 计划提交、修订与安全阻断的验收测试。"""

from collections import deque
from typing import Any
from uuid import uuid4

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


class QueueActionProvider:
    """按顺序返回模型动作，避免测试依赖真实模型。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.calls = 0

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一动作；队列耗尽表示图发生了意外循环。"""
        del state
        self.calls += 1
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class FixedToolExecutor:
    """记录工具调用并返回固定观察结果。"""

    def __init__(self) -> None:
        self.actions: list[AgentAction] = []

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """保存动作，供断言计划不会绕过正常工具路由。"""
        self.actions.append(action)
        return TOOL_OBSERVATION


def plan_action(*, items: list[PlanItem], reason: str) -> AgentAction:
    """构造由模型提交、等待 Harness 校验的完整计划。"""
    return AgentAction(
        action_type=ActionType.UPDATE_PLAN,
        intent="更新当前诊断计划",
        reason=reason,
        plan=items,
    )


def tool_action() -> AgentAction:
    """构造低风险指标查询动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务延迟",
        tool_name=TOOL_NAME,
        reason="需要收集延迟指标证据。",
    )


def final_action() -> AgentAction:
    """构造引用指标证据的最终报告动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出当前诊断结论",
        reason="指标证据已经足够。",
        report=report_for_observation(
            tool_name=TOOL_NAME,
            observation=TOOL_OBSERVATION,
        ),
    )


def make_state() -> dict[str, Any]:
    """构造能容纳两次计划提交的测试预算。"""
    return create_initial_state(
        session_id="session-plan",
        thread_id="thread-plan",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=5,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


def make_loop(provider: QueueActionProvider, executor: FixedToolExecutor) -> HarnessLoop:
    """构造使用低风险指标工具的 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.LOW)]),
    )


@pytest.mark.asyncio
async def test_harness_applies_initial_plan_before_tool_execution() -> None:
    """首个 update_plan 必须形成计划版本，之后才能继续工具诊断。"""
    initial_items = [
        PlanItem(title="确认接口延迟", rationale="验证用户报告的现象"),
        PlanItem(title="查询指标", rationale="收集延迟证据"),
    ]
    provider = QueueActionProvider(
        [
            plan_action(items=initial_items, reason="先建立可审计诊断步骤。"),
            tool_action(),
            final_action(),
        ]
    )
    executor = FixedToolExecutor()

    result = await make_loop(provider, executor).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["plan_version"] == 1
    assert len(result["plan_history"]) == 1
    assert result["plan"] == initial_items
    assert provider.calls == 3
    assert [action.tool_name for action in executor.actions] == [TOOL_NAME]
    assert result["budget"].used_steps == 3
    assert EventType.PLAN_CREATED in [event.event_type for event in result["trajectory"]]


@pytest.mark.asyncio
async def test_harness_recovers_when_model_repeats_a_completed_plan_item() -> None:
    """已完成计划项的重复工具调用应被拒绝，并允许模型改为输出报告。"""
    item = PlanItem(title="查询指标", rationale="收集诊断证据")
    provider = QueueActionProvider(
        [
            plan_action(items=[item], reason="建立诊断计划。"),
            AgentAction(
                action_type=ActionType.CALL_TOOL,
                intent="查询支付服务延迟",
                tool_name=TOOL_NAME,
                reason="收集指标证据。",
                plan_item_id=item.id,
            ),
            AgentAction(
                action_type=ActionType.CALL_TOOL,
                intent="重复查询支付服务延迟",
                tool_name=TOOL_NAME,
                reason="错误地重复调用已完成计划项。",
                plan_item_id=item.id,
            ),
            final_action(),
        ]
    )
    executor = FixedToolExecutor()

    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.LOW)]),
        max_policy_corrections=1,
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.calls == 4
    assert [action.tool_name for action in executor.actions] == [TOOL_NAME]
    assert result["replan_feedback"] is None
    assert any(
        event.event_type is EventType.ACTION_BLOCKED
        and event.decision == "plan item cannot be started from its current status"
        for event in result["trajectory"]
    )


@pytest.mark.asyncio
async def test_harness_recovers_when_model_reaches_a_tool_call_limit() -> None:
    """达到工具调用上限时，拒绝当前调用并允许模型改为输出报告。"""
    provider = QueueActionProvider(
        [
            AgentAction(
                action_type=ActionType.CALL_TOOL,
                intent="查询第一个指标",
                tool_name=TOOL_NAME,
                tool_args={"scope": "first"},
                reason="收集第一条证据。",
            ),
            AgentAction(
                action_type=ActionType.CALL_TOOL,
                intent="查询第二个指标",
                tool_name=TOOL_NAME,
                tool_args={"scope": "second"},
                reason="错误地超过单工具上限。",
            ),
            final_action(),
        ]
    )
    executor = FixedToolExecutor()
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy(
            [ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.LOW, max_calls_per_run=1)]
        ),
        max_policy_corrections=1,
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.calls == 3
    assert [action.tool_args for action in executor.actions] == [{"scope": "first"}]
    assert result["replan_feedback"] is None


@pytest.mark.asyncio
async def test_harness_preserves_versions_when_plan_is_revised() -> None:
    """后续 update_plan 应保留旧版本，并记录 PLAN_REVISED 事件。"""
    first_items = [PlanItem(title="查询接口延迟", rationale="确认症状")]
    revised_items = [
        PlanItem(title="查询数据库连接池", rationale="指标提示数据库瓶颈"),
        PlanItem(title="输出诊断报告", rationale="汇总已收集证据"),
    ]
    provider = QueueActionProvider(
        [
            plan_action(items=first_items, reason="建立初始计划。"),
            tool_action(),
            plan_action(items=revised_items, reason="根据指标调整诊断路径。"),
            final_action(),
        ]
    )
    executor = FixedToolExecutor()

    result = await make_loop(provider, executor).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["plan_version"] == 2
    assert [revision.version for revision in result["plan_history"]] == [1, 2]
    assert result["plan_history"][0].items == first_items
    assert result["plan"] == revised_items
    assert result["replan_requested"] is False
    assert result["replan_count"] == 1
    assert EventType.PLAN_REVISED in [event.event_type for event in result["trajectory"]]


@pytest.mark.asyncio
async def test_harness_blocks_model_plan_with_unknown_dependency() -> None:
    """依赖不存在的模型计划必须被 Harness 阻断，不能进入工具节点。"""
    invalid_items = [
        PlanItem(
            title="查询指标",
            rationale="测试未知依赖阻断。",
            depends_on=[uuid4()],
        )
    ]
    provider = QueueActionProvider([plan_action(items=invalid_items, reason="提交一个无效计划。")])
    executor = FixedToolExecutor()

    result = await make_loop(provider, executor).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["errors"][-1] == "plan item depends on an unknown item"
    assert executor.actions == []
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_harness_blocks_plan_revisions_after_replan_limit() -> None:
    """初始计划不计入，达到重规划上限后不得继续修改计划。"""
    provider = QueueActionProvider(
        [
            plan_action(
                items=[PlanItem(title="初始计划", rationale="建立第一条诊断路径。")],
                reason="建立初始计划。",
            ),
            plan_action(
                items=[PlanItem(title="第一次修订", rationale="根据新线索调整路径。")],
                reason="应用第一条新线索。",
            ),
            plan_action(
                items=[PlanItem(title="第二次修订", rationale="尝试再次调整路径。")],
                reason="尝试第二次修订。",
            ),
        ]
    )
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.LOW)]),
        max_replans=1,
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["plan_version"] == 2
    assert result["replan_count"] == 1
    assert result["errors"][-1] == "本次运行已达到重新规划上限。"
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
