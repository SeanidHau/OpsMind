"""ContextManager 的验收测试。"""

from copy import deepcopy

import pytest

from app.harness.context import ContextManager
from app.harness.loop import create_initial_state
from app.models.contracts import BudgetState, ContextSource, EvidenceItem, PlanItem


def make_state() -> dict[str, object]:
    """构造包含计划、证据与工具结果的上下文输入。"""
    state = create_initial_state(
        session_id="session-1",
        thread_id="thread-1",
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
    state["plan"] = [
        PlanItem(
            title="确认支付服务错误率",
            rationale="先验证是否存在服务端异常。",
        )
    ]
    state["evidence"] = [
        EvidenceItem(
            evidence_id="e" * 64,
            tool_name="query_metrics",
            content='{"error_rate":0.12}',
        )
    ]
    state["tool_results"] = [
        {"tool_name": "query_metrics", "result": {"error_rate": 0.12}},
        {"tool_name": "query_metrics", "result": {"error_rate": 0.12}},
    ]
    return state


def test_build_keeps_task_context_and_deduplicates_tool_results() -> None:
    """上下文必须保留任务，且相同工具观察只保留一份。"""
    state = make_state()
    original_state = deepcopy(state)

    snapshot = ContextManager(max_chars=1_000, max_items=10).build(state)  # type: ignore[arg-type]

    assert snapshot.items[0].source is ContextSource.TASK
    assert snapshot.items[0].reference == "user_query"
    assert f"evidence:{'e' * 64}" in [item.reference for item in snapshot.items]
    assert sum(item.source is ContextSource.TOOL_RESULT for item in snapshot.items) == 1
    assert state == original_state


def test_build_respects_character_budget_and_marks_truncation() -> None:
    """字符预算不足时，任务仍被保留且快照明确标记截断。"""
    state = make_state()
    state["user_query"] = "超时" * 100

    snapshot = ContextManager(max_chars=40, max_items=5).build(state)  # type: ignore[arg-type]

    assert snapshot.total_chars <= 40
    assert snapshot.truncated is True
    assert snapshot.items[0].source is ContextSource.TASK


def test_build_includes_recent_conversation_without_mutating_state() -> None:
    """最近用户澄清应进入最小上下文，完整会话保持在运行状态中。"""
    state = make_state()
    state["conversation"] = [
        {"role": "assistant", "content": "故障大约从何时开始？"},
        {"role": "user", "content": "今天 10:15 左右开始变慢。"},
    ]
    original_state = deepcopy(state)

    snapshot = ContextManager(max_chars=1_000, max_items=10).build(state)  # type: ignore[arg-type]

    assert [
        item.content for item in snapshot.items if item.source is ContextSource.CONVERSATION
    ] == [
        "assistant: 故障大约从何时开始？",
        "user: 今天 10:15 左右开始变慢。",
    ]
    assert state == original_state


def test_invalid_limits_are_rejected() -> None:
    """无效上下文限制应在启动阶段失败，而不是运行时静默降级。"""
    with pytest.raises(ValueError, match="max_chars"):
        ContextManager(max_chars=0)

    with pytest.raises(ValueError, match="max_items"):
        ContextManager(max_items=0)
