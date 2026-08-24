"""已归档轨迹确定性评测的验收测试。"""

from uuid import UUID, uuid4

from app.harness.evaluation import TrajectoryEvaluator
from app.models.contracts import (
    AgentEvent,
    BudgetState,
    DiagnosisReport,
    EventType,
    EvidenceItem,
    HarnessStatus,
    RunSnapshot,
)


def event(run_id: UUID, event_type: EventType, step_id: int) -> AgentEvent:
    """构造最小审计事件。"""
    return AgentEvent(run_id=run_id, event_type=event_type, step_id=step_id)


def valid_snapshot() -> RunSnapshot:
    """构造带完整证据、报告和 checkpoint 的完成运行快照。"""
    run_id = uuid4()
    evidence = EvidenceItem(
        evidence_id="a" * 64,
        tool_name="query_metrics",
        content='{"error_rate":0.12}',
    )
    report = DiagnosisReport(
        summary="支付服务延迟升高。",
        probable_root_cause="数据库连接池耗尽。",
        confidence=0.8,
        evidence_ids=[evidence.evidence_id],
        recommended_actions=["检查连接池上限。"],
    )
    budget = BudgetState(
        max_steps=5,
        max_tool_calls=3,
        max_model_calls=3,
        max_tokens=1_000,
        max_runtime_seconds=60,
        max_estimated_cost_usd=1.0,
        used_steps=2,
        used_tool_calls=1,
        used_model_calls=2,
    )

    return RunSnapshot(
        run_id=run_id,
        session_id="session-evaluation",
        thread_id="thread-evaluation",
        terminal_status=HarnessStatus.COMPLETED,
        final_state={
            "budget": budget.model_dump(mode="json"),
            "diagnosis_report": report.model_dump(mode="json"),
            "evidence": [evidence.model_dump(mode="json")],
        },
        trajectory=[
            event(run_id, EventType.RUN_COMPLETED, 2),
            event(run_id, EventType.CHECKPOINT_SAVED, 2),
        ],
    )


def checks_by_name(snapshot: RunSnapshot) -> dict[str, bool]:
    """以检查名称索引当前快照的评测结果。"""
    return {check.name: check.passed for check in TrajectoryEvaluator().evaluate(snapshot).checks}


def test_evaluator_accepts_complete_trace_with_valid_evidence_report() -> None:
    """完整、可追溯且满足预算的完成运行应得到满分。"""
    evaluation = TrajectoryEvaluator().evaluate(valid_snapshot())

    assert evaluation.passed is True
    assert evaluation.score == 1.0


def test_evaluator_rejects_trace_missing_checkpoint() -> None:
    """没有归档 checkpoint 的轨迹不能视为可重放运行。"""
    snapshot = valid_snapshot()
    snapshot.trajectory.pop()

    checks = checks_by_name(snapshot)

    assert checks["checkpoint_saved"] is False
    assert checks["terminal_event"] is True


def test_evaluator_rejects_completed_report_with_unknown_evidence() -> None:
    """完成报告引用不存在的证据时必须失败。"""
    snapshot = valid_snapshot()
    snapshot.final_state["diagnosis_report"]["evidence_ids"] = ["b" * 64]

    assert checks_by_name(snapshot)["completed_report_evidence"] is False


def test_evaluator_rejects_budget_exceeding_its_limit() -> None:
    """快照中的超限预算不得通过离线评测。"""
    snapshot = valid_snapshot()
    snapshot.final_state["budget"]["used_steps"] = 6

    assert checks_by_name(snapshot)["budget_within_limits"] is False
