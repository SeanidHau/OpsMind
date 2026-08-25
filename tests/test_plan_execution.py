"""计划项绑定、依赖检查与状态迁移的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.plan import PlanManager
from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    HarnessStatus,
    PlanItem,
    PlanStatus,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation


class QueueActionProvider:
    """按固定顺序返回动作，避免测试依赖模型输出。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一动作；队列耗尽表示图产生了意外循环。"""
        del state
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class FixedToolExecutor:
    """返回固定观察结果的低风险工具。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """使用工具名构造可引用观察结果。"""
        return {"status": "ok", "tool_name": action.tool_name}


def make_state() -> dict[str, Any]:
    """构造可执行计划、工具和最终报告的状态。"""
    return create_initial_state(
        session_id="session-plan-execution",
        thread_id="thread-plan-execution",
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


def test_plan_manager_requires_completed_dependencies_before_starting_item() -> None:
    """依赖项未完成时，后续计划项不能进入执行状态。"""
    prerequisite = PlanItem(title="确认症状", rationale="先验证用户报告。")
    dependent = PlanItem(
        title="查询指标",
        rationale="症状确认后收集性能证据。",
        depends_on=[prerequisite.id],
    )

    with pytest.raises(ValueError, match="dependencies are not completed"):
        PlanManager.start_item([prerequisite, dependent], dependent.id)


@pytest.mark.asyncio
async def test_harness_completes_bound_plan_item_after_successful_tool() -> None:
    """获准工具成功后，绑定计划项必须从 pending 迁移到 completed。"""
    plan_item = PlanItem(title="查询指标", rationale="收集延迟证据。")
    update_plan = AgentAction(
        action_type=ActionType.UPDATE_PLAN,
        intent="建立诊断计划",
        reason="先收集支付服务指标。",
        plan=[plan_item],
    )
    tool_action = AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name="query_metrics",
        plan_item_id=plan_item.id,
        reason="执行计划项并收集性能证据。",
    )
    final_action = AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="证据已收集。",
        report=report_for_observation(
            tool_name="query_metrics",
            observation={"status": "ok", "tool_name": "query_metrics"},
        ),
    )
    loop = HarnessLoop(
        action_provider=QueueActionProvider([update_plan, tool_action, final_action]),
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["plan"][0].status is PlanStatus.COMPLETED


@pytest.mark.asyncio
async def test_harness_blocks_bound_action_when_dependency_is_pending() -> None:
    """未完成依赖的计划项不能进入工具执行器。"""
    prerequisite = PlanItem(title="确认症状", rationale="先验证用户报告。")
    dependent = PlanItem(
        title="查询指标",
        rationale="症状确认后收集性能证据。",
        depends_on=[prerequisite.id],
    )
    update_plan = AgentAction(
        action_type=ActionType.UPDATE_PLAN,
        intent="建立诊断计划",
        reason="先确认症状，再查询指标。",
        plan=[prerequisite, dependent],
    )
    blocked_action = AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name="query_metrics",
        plan_item_id=dependent.id,
        reason="尝试越过前置计划项。",
    )
    loop = HarnessLoop(
        action_provider=QueueActionProvider([update_plan, blocked_action]),
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["errors"][-1] == "plan item dependencies are not completed"
