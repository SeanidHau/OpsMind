"""离线 benchmark 聚合与样本期望的验收测试。"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.harness.benchmark import (
    HarnessBenchmarkSubject,
    OfflineBenchmarkRunner,
    load_benchmark_cases,
)
from app.harness.loop import create_initial_state
from app.models.contracts import (
    AgentEvent,
    BudgetState,
    DiagnosisReport,
    DiagnosisState,
    EvaluationCase,
    EventType,
    EvidenceItem,
    HarnessStatus,
    RunSnapshot,
)
from scripts.run_benchmark import benchmark_exit_code


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


def test_loader_reads_committed_diagnosis_cases() -> None:
    """提交的端到端样本必须可被统一评测契约读取。"""
    cases = load_benchmark_cases(Path("data/evaluations/diagnosis_cases.json"))

    assert [case.case_id for case in cases] == [
        "order-http-5xx",
        "payment-connection-pool",
        "inventory-latency",
        "recommendation-redis-cache",
    ]


def test_loader_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    """重复样本标识会使批量结果不可追溯，必须拒绝。"""
    path = tmp_path / "cases.json"
    path.write_text(
        "["
        '{"case_id":"duplicate","user_query":"first","expected_terminal_status":"completed"},'
        '{"case_id":"duplicate","user_query":"second","expected_terminal_status":"completed"}'
        "]",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="case_id values must be unique"):
        load_benchmark_cases(path)


@pytest.mark.asyncio
async def test_harness_subject_converts_runner_state_to_snapshot() -> None:
    """端到端适配器必须保留样本标识和运行快照中的查询。"""
    state = create_initial_state(
        session_id="source-session",
        thread_id="source-thread",
        user_query="source query",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )

    class RecordingRunner:
        def __init__(self) -> None:
            self.arguments: dict[str, str] | None = None

        async def run(
            self,
            *,
            session_id: str,
            thread_id: str,
            user_query: str,
        ) -> DiagnosisState:
            self.arguments = {
                "session_id": session_id,
                "thread_id": thread_id,
                "user_query": user_query,
            }
            return state

    runner = RecordingRunner()
    snapshot = await HarnessBenchmarkSubject(runner=runner).run_case(case("payment-timeout"))

    assert runner.arguments == {
        "session_id": "benchmark-payment-timeout",
        "thread_id": "benchmark-payment-timeout",
        "user_query": "支付服务请求超时",
    }
    assert snapshot.run_id == UUID(state["run_id"])
    assert snapshot.final_state["user_query"] == "source query"


@pytest.mark.parametrize(
    ("passed", "fail_on_failure", "expected_exit_code"),
    [
        (True, False, 0),
        (True, True, 0),
        (False, False, 0),
        (False, True, 1),
    ],
)
def test_benchmark_quality_gate_is_opt_in(
    passed: bool,
    fail_on_failure: bool,
    expected_exit_code: int,
) -> None:
    """本地观察不阻断，CI 可显式将失败样本视为失败。"""
    assert (
        benchmark_exit_code(
            passed=passed,
            fail_on_failure=fail_on_failure,
        )
        == expected_exit_code
    )
