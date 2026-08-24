"""离线 benchmark 聚合与样本期望的验收测试。"""

from uuid import UUID, uuid4

import pytest

from app.harness.benchmark import OfflineBenchmarkRunner
from app.models.contracts import (
    AgentEvent,
    BudgetState,
    DiagnosisReport,
    EvaluationCase,
    EventType,
    EvidenceItem,
    HarnessStatus,
    RunSnapshot,
)


def event(run_id: UUID, event_type: EventType, step_id: int) -> AgentEvent:
    """构造最小审计事件。"""
    return AgentEvent(run_id=run_id, event_type=event_type, step_id=step_id)


def completed_snapshot() -> RunSnapshot:
    """构造可通过轨迹评测的完成运行快照。"""
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
        session_id="session-benchmark",
        thread_id="thread-benchmark",
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


class FixedBenchmarkSubject:
    """为所有样本返回同一稳定快照的被测对象。"""

    def __init__(self, snapshot: RunSnapshot) -> None:
        self._snapshot = snapshot
        self.case_ids: list[str] = []

    async def run_case(self, case: EvaluationCase) -> RunSnapshot:
        """记录样本调用，模拟外部 Harness 执行。"""
        self.case_ids.append(case.case_id)
        return self._snapshot


def case(
    case_id: str,
    *,
    root_cause: str | None = "连接池",
    evidence_tools: tuple[str, ...] = ("query_metrics",),
) -> EvaluationCase:
    """构造以支付服务超时为目标的离线评测样本。"""
    return EvaluationCase(
        case_id=case_id,
        user_query="支付服务请求超时",
        expected_terminal_status=HarnessStatus.COMPLETED,
        expected_root_cause_contains=root_cause,
        expected_evidence_tools=evidence_tools,
    )


def checks_by_name(result: object) -> dict[str, bool]:
    """以检查名称索引单样本结果。"""
    return {check.name: check.passed for check in result.checks}  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_benchmark_aggregates_multiple_passing_cases() -> None:
    """多个通过样本应得到满分、通过的批量结果。"""
    subject = FixedBenchmarkSubject(completed_snapshot())

    result = await OfflineBenchmarkRunner().run(
        cases=[case("payment-timeout-1"), case("payment-timeout-2")],
        subject=subject,
    )

    assert result.passed is True
    assert result.score == 1.0
    assert subject.case_ids == ["payment-timeout-1", "payment-timeout-2"]


@pytest.mark.asyncio
async def test_benchmark_reports_failed_business_expectations() -> None:
    """根因或工具期望不满足时，结果应保留具体失败检查。"""
    result = await OfflineBenchmarkRunner().run(
        cases=[case("wrong-expectation", root_cause="Redis", evidence_tools=("query_logs",))],
        subject=FixedBenchmarkSubject(completed_snapshot()),
    )

    assert result.passed is False
    checks = checks_by_name(result.case_results[0])
    assert checks["expected_root_cause"] is False
    assert checks["expected_evidence_tools"] is False


@pytest.mark.asyncio
async def test_benchmark_rejects_empty_case_list() -> None:
    """空样本集不能生成没有统计意义的 benchmark 分数。"""
    with pytest.raises(ValueError, match="cases must not be empty"):
        await OfflineBenchmarkRunner().run(
            cases=[],
            subject=FixedBenchmarkSubject(completed_snapshot()),
        )
