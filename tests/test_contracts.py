"""Harness 核心契约的验收测试。

这些测试固定跨模块交换的数据格式。业务实现位于 app/models/contracts.py，
由项目开发者按测试约束实现。
"""

from datetime import UTC, datetime
from typing import get_type_hints
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    BudgetState,
    DiagnosisReport,
    DiagnosisState,
    EventType,
    PlanItem,
    PlanRevision,
    PlanStatus,
)


def test_tool_action_requires_a_tool_name() -> None:
    """call_tool 动作必须明确指定工具名称。"""
    action = AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询订单服务的 P99 延迟",
        tool_name="query_metrics",
        tool_args={"service": "order-service"},
        expected_observation="返回延迟趋势",
        reason="先确认延迟是否异常",
    )

    assert action.tool_name == "query_metrics"

    with pytest.raises(ValidationError, match="tool_name"):
        AgentAction(
            action_type=ActionType.CALL_TOOL,
            intent="查询指标",
            reason="缺少工具名称时不得执行",
        )


def test_non_tool_action_rejects_tool_metadata() -> None:
    """非工具动作不能携带工具名称或参数，避免模型输出语义不一致。"""
    with pytest.raises(ValidationError, match="tool"):
        AgentAction(
            action_type=ActionType.FINAL_ANSWER,
            intent="生成诊断结论",
            tool_name="query_metrics",
            reason="最终回答不应调用工具",
        )


def test_final_answer_requires_a_structured_report() -> None:
    """最终回答必须提供至少一条证据引用的结构化报告。"""
    with pytest.raises(ValidationError, match="report is required"):
        AgentAction(
            action_type=ActionType.FINAL_ANSWER,
            intent="生成诊断结论",
            reason="证据已满足门槛",
        )

    action = AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="生成诊断结论",
        reason="证据已满足门槛",
        report=DiagnosisReport(
            summary="支付服务超时。",
            probable_root_cause="数据库连接池耗尽。",
            confidence=0.8,
            evidence_ids=["a" * 64],
            recommended_actions=["检查连接池。"],
        ),
    )

    assert action.report is not None


def test_update_plan_requires_plan_and_other_actions_reject_it() -> None:
    """计划载荷只能由 update_plan 提交，避免动作语义混杂。"""
    item = PlanItem(title="查询指标", rationale="收集延迟证据")
    action = AgentAction(
        action_type=ActionType.UPDATE_PLAN,
        intent="建立诊断计划",
        reason="开始排查前先明确步骤。",
        plan=[item],
    )

    assert action.plan == [item]
    with pytest.raises(ValidationError, match="plan is required"):
        AgentAction(
            action_type=ActionType.UPDATE_PLAN,
            intent="建立诊断计划",
            reason="缺少计划载荷。",
        )
    with pytest.raises(ValidationError, match="plan is only allowed"):
        AgentAction(
            action_type=ActionType.ASK_USER,
            intent="补充故障时间",
            question="故障大约从何时开始？",
            reason="需要缩小时间窗口。",
            plan=[item],
        )


def test_budget_state_tracks_remaining_capacity() -> None:
    """预算状态必须拒绝已使用量超过上限的无效配置。"""
    budget = BudgetState(
        max_steps=5,
        max_tool_calls=3,
        max_model_calls=4,
        max_tokens=1_000,
        max_runtime_seconds=60,
        max_estimated_cost_usd=0.5,
        used_steps=2,
        used_tool_calls=1,
        used_model_calls=1,
        used_tokens=250,
        used_runtime_seconds=10,
        used_estimated_cost_usd=0.1,
    )

    assert budget.remaining_steps == 3
    assert budget.remaining_tool_calls == 2
    assert budget.remaining_tokens == 750

    with pytest.raises(ValidationError, match="used_steps"):
        BudgetState(
            max_steps=1,
            max_tool_calls=1,
            max_model_calls=1,
            max_tokens=1,
            max_runtime_seconds=1,
            max_estimated_cost_usd=0.0,
            used_steps=2,
        )


def test_event_uses_utc_timestamp_and_serializes_nested_action() -> None:
    """轨迹事件默认使用 UTC 时间，并保留结构化动作记录。"""
    action = AgentAction(
        action_type=ActionType.ASK_USER,
        intent="补充故障发生时间",
        question="故障大约从何时开始？",
        reason="当前日志检索缺少时间窗口",
    )
    event = AgentEvent(
        run_id=uuid4(),
        step_id=1,
        event_type=EventType.ACTION_PROPOSED,
        action=action,
    )

    assert event.timestamp.tzinfo == UTC
    assert event.model_dump(mode="json")["action"]["action_type"] == "ask_user"


def test_ask_user_requires_question_and_rejects_question_on_other_actions() -> None:
    """澄清问题只能由 ask_user 动作携带。"""
    with pytest.raises(ValidationError, match="question is required"):
        AgentAction(
            action_type=ActionType.ASK_USER,
            intent="确认时间窗口",
            reason="需要缩小日志范围。",
        )

    with pytest.raises(ValidationError, match="question is only allowed"):
        AgentAction(
            action_type=ActionType.FAIL,
            intent="结束诊断",
            reason="无法继续。",
            question="这不应被允许。",
        )


def test_plan_and_graph_state_expose_required_fields() -> None:
    """计划项默认待处理，Graph State 必须暴露 Harness 所需的关键字段。"""
    plan_item = PlanItem(title="查询接口延迟", rationale="验证用户报告的慢请求")
    state_fields = get_type_hints(DiagnosisState)

    assert plan_item.status == PlanStatus.PENDING
    revision = PlanRevision(version=1, reason="建立初始计划。", items=[plan_item])

    assert revision.items == [plan_item]
    assert {"plan", "plan_history", "budget", "trajectory", "progress_status"}.issubset(
        state_fields
    )
    assert datetime.now(UTC).tzinfo == UTC
