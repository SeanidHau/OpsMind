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
    DiagnosisState,
    EventType,
    PlanItem,
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


def test_plan_and_graph_state_expose_required_fields() -> None:
    """计划项默认待处理，Graph State 必须暴露 Harness 所需的关键字段。"""
    plan_item = PlanItem(title="查询接口延迟", rationale="验证用户报告的慢请求")
    state_fields = get_type_hints(DiagnosisState)

    assert plan_item.status == PlanStatus.PENDING
    assert {"plan", "budget", "trajectory", "progress_status"}.issubset(state_fields)
    assert datetime.now(UTC).tzinfo == UTC
