"""基于运行快照的可重复离线 benchmark。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from app.harness.evaluation import TrajectoryEvaluator
from app.harness.snapshot import RunSnapshotFactory
from app.models.contracts import (
    BenchmarkCaseResult,
    BenchmarkMetrics,
    BenchmarkResult,
    DiagnosisState,
    EvaluationCase,
    EvaluationCheck,
    EventType,
    HarnessStatus,
    RunSnapshot,
)


class BenchmarkSubject(Protocol):
    """能够为一个评测样本产出已归档运行快照的被测对象。"""

    async def run_case(self, case: EvaluationCase) -> RunSnapshot:
        """执行样本并返回可供离线评测的快照。"""


class DiagnosisSubject(Protocol):
    """端到端基准运行所需的最小诊断执行接口。"""

    async def run(
        self,
        *,
        session_id: str,
        thread_id: str,
        user_query: str,
    ) -> DiagnosisState:
        """执行一轮诊断并返回已经归档的最终状态。"""


def load_benchmark_cases(path: Path) -> list[EvaluationCase]:
    """从 JSON 数组读取端到端基准样本，并拒绝重复标识。"""
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("benchmark cases must be a non-empty JSON array")

    cases = [EvaluationCase.model_validate(case) for case in raw_cases]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("benchmark case_id values must be unique")
    return cases


class HarnessBenchmarkSubject:
    """将应用诊断运行器适配为离线基准的快照来源。"""

    def __init__(self, *, runner: DiagnosisSubject) -> None:
        """注入应用已装配完成的诊断运行器。"""
        self._runner = runner
        self._snapshot_factory = RunSnapshotFactory()

    async def run_case(self, case: EvaluationCase) -> RunSnapshot:
        """使用稳定会话标识执行样本，并转换归档状态为快照。"""
        state = await self._runner.run(
            session_id=f"benchmark-{case.case_id}",
            thread_id=f"benchmark-{case.case_id}",
            user_query=case.user_query,
        )
        return self._snapshot_factory.build(state)


class OfflineBenchmarkRunner:
    """组合轨迹评测和样本业务期望，得到可比较的批量结果。"""

    def __init__(self, *, trajectory_evaluator: TrajectoryEvaluator | None = None) -> None:
        """注入轨迹评测器，便于替换或扩展评分规则。"""
        self._trajectory_evaluator = trajectory_evaluator or TrajectoryEvaluator()

    async def run(
        self,
        *,
        cases: list[EvaluationCase],
        subject: BenchmarkSubject,
    ) -> BenchmarkResult:
        """顺序执行样本，避免并发导致工具或成本数据相互干扰。"""
        if not cases:
            raise ValueError("cases must not be empty")

        snapshots = [await subject.run_case(case) for case in cases]
        case_results = [
            self._evaluate_case(case=case, snapshot=snapshot)
            for case, snapshot in zip(cases, snapshots, strict=True)
        ]
        score = sum(result.score for result in case_results) / len(case_results)

        return BenchmarkResult(
            passed=all(result.passed for result in case_results),
            score=score,
            case_results=case_results,
            metrics=self._build_metrics(case_results=case_results, snapshots=snapshots),
        )

    @staticmethod
    def _build_metrics(
        *,
        case_results: list[BenchmarkCaseResult],
        snapshots: list[RunSnapshot],
    ) -> BenchmarkMetrics:
        """从已评测快照提取实验对比所需的稳定统计指标。"""
        run_count = len(snapshots)
        completed_run_count = sum(
            snapshot.terminal_status is HarnessStatus.COMPLETED for snapshot in snapshots
        )
        trajectory_pass_count = sum(
            case_result.trajectory_evaluation.passed for case_result in case_results
        )
        tool_call_counts = [
            OfflineBenchmarkRunner._non_negative_int(snapshot.final_state.get("tool_call_count"))
            for snapshot in snapshots
        ]
        model_call_counts = [
            OfflineBenchmarkRunner._budget_value(snapshot, "used_model_calls")
            for snapshot in snapshots
        ]
        used_tokens = [
            OfflineBenchmarkRunner._budget_value(snapshot, "used_tokens") for snapshot in snapshots
        ]
        context_sizes = [
            int(event.observation["total_chars"])
            for snapshot in snapshots
            for event in snapshot.trajectory
            if event.event_type is EventType.CONTEXT_BUILT
            and isinstance(event.observation, dict)
            and isinstance(event.observation.get("total_chars"), int)
            and event.observation["total_chars"] >= 0
        ]
        tool_fingerprints = [
            json.dumps(
                {"tool_name": event.action.tool_name, "tool_args": event.action.tool_args},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for snapshot in snapshots
            for event in snapshot.trajectory
            if event.event_type is EventType.TOOL_FINISHED and event.action is not None
        ]
        duplicate_tool_calls = len(tool_fingerprints) - len(set(tool_fingerprints))
        terminal_status_counts = Counter(
            snapshot.terminal_status.value if snapshot.terminal_status is not None else "unknown"
            for snapshot in snapshots
        )

        return BenchmarkMetrics(
            run_count=run_count,
            completed_run_count=completed_run_count,
            completion_rate=completed_run_count / run_count,
            trajectory_pass_rate=trajectory_pass_count / run_count,
            average_tool_calls=sum(tool_call_counts) / run_count,
            duplicate_tool_call_rate=(
                duplicate_tool_calls / len(tool_fingerprints) if tool_fingerprints else 0
            ),
            average_model_calls=sum(model_call_counts) / run_count,
            average_used_tokens=sum(used_tokens) / run_count,
            average_context_chars=(sum(context_sizes) / len(context_sizes) if context_sizes else 0),
            terminal_status_counts=dict(sorted(terminal_status_counts.items())),
        )

    @staticmethod
    def _budget_value(snapshot: RunSnapshot, field_name: str) -> int:
        """从已归档预算中读取非负数值；无效历史快照按零计。"""
        budget = snapshot.final_state.get("budget")
        return (
            OfflineBenchmarkRunner._non_negative_int(budget.get(field_name))
            if isinstance(budget, dict)
            else 0
        )

    @staticmethod
    def _non_negative_int(value: object) -> int:
        """将快照中的未知数值安全转换为非负整数。"""
        if not isinstance(value, int | float | str):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _evaluate_case(
        self,
        *,
        case: EvaluationCase,
        snapshot: RunSnapshot,
    ) -> BenchmarkCaseResult:
        """评测单个快照的轨迹质量和样本特定期望。"""
        trajectory_evaluation = self._trajectory_evaluator.evaluate(snapshot)
        checks = [
            *trajectory_evaluation.checks,
            self._check_expected_status(case, snapshot),
            self._check_root_cause(case, snapshot),
            self._check_evidence_tools(case, snapshot),
        ]
        score = sum(check.passed for check in checks) / len(checks)

        return BenchmarkCaseResult(
            case_id=case.case_id,
            passed=all(check.passed for check in checks),
            score=score,
            checks=checks,
            trajectory_evaluation=trajectory_evaluation,
        )

    @staticmethod
    def _check_expected_status(
        case: EvaluationCase,
        snapshot: RunSnapshot,
    ) -> EvaluationCheck:
        """确认运行的终止状态符合样本预期。"""
        passed = snapshot.terminal_status is case.expected_terminal_status
        return EvaluationCheck(
            name="expected_terminal_status",
            passed=passed,
            detail="终止状态符合样本预期。"
            if passed
            else (
                f"终止状态不匹配：期望 {case.expected_terminal_status}，"
                f"实际 {snapshot.terminal_status}。"
            ),
        )

    @staticmethod
    def _check_root_cause(
        case: EvaluationCase,
        snapshot: RunSnapshot,
    ) -> EvaluationCheck:
        """检查完成报告的根因是否包含样本要求的关键词。"""
        expected = case.expected_root_cause_contains
        if expected is None:
            return EvaluationCheck(
                name="expected_root_cause",
                passed=True,
                detail="样本未声明根因关键词，不适用检查。",
            )

        report = snapshot.final_state.get("diagnosis_report")
        actual = report.get("probable_root_cause") if isinstance(report, dict) else None
        passed = isinstance(actual, str) and expected.casefold() in actual.casefold()

        return EvaluationCheck(
            name="expected_root_cause",
            passed=passed,
            detail="报告根因包含预期关键词。"
            if passed
            else f"报告根因未包含预期关键词：{expected}。",
        )

    @staticmethod
    def _check_evidence_tools(
        case: EvaluationCase,
        snapshot: RunSnapshot,
    ) -> EvaluationCheck:
        """检查完成运行是否收集到样本要求的工具证据。"""
        expected_tools = set(case.expected_evidence_tools)
        if not expected_tools:
            return EvaluationCheck(
                name="expected_evidence_tools",
                passed=True,
                detail="样本未声明必需工具，不适用检查。",
            )

        evidence = snapshot.final_state.get("evidence", [])
        actual_tools = {
            item.get("tool_name")
            for item in evidence
            if isinstance(item, dict) and isinstance(item.get("tool_name"), str)
        }
        missing_tools = sorted(expected_tools - actual_tools)
        passed = not missing_tools

        return EvaluationCheck(
            name="expected_evidence_tools",
            passed=passed,
            detail="已收集全部必需工具证据。"
            if passed
            else f"缺少必需工具证据：{', '.join(missing_tools)}。",
        )


COMPARABLE_METRIC_FIELDS = (
    "run_count",
    "completed_run_count",
    "completion_rate",
    "trajectory_pass_rate",
    "average_tool_calls",
    "duplicate_tool_call_rate",
    "average_model_calls",
    "average_used_tokens",
    "average_context_chars",
)


def compare_benchmark_results(results: Mapping[str, BenchmarkResult]) -> dict[str, Any]:
    """以 full 配置为基准，汇总同一批样本的实验差异。"""
    baseline = results.get("full")
    if baseline is None:
        raise ValueError("benchmark comparison requires a full profile result")

    case_ids = [case_result.case_id for case_result in baseline.case_results]
    for profile, result in results.items():
        if [case_result.case_id for case_result in result.case_results] != case_ids:
            raise ValueError(f"benchmark case IDs for profile {profile} do not match full")

    baseline_metrics = baseline.metrics.model_dump()
    profiles = []
    for profile in ["full", *sorted(name for name in results if name != "full")]:
        result = results[profile]
        metrics = result.metrics.model_dump()
        profiles.append(
            {
                "profile": profile,
                "passed": result.passed,
                "score": result.score,
                "metrics": metrics,
                "deltas_from_full": {
                    "score": result.score - baseline.score,
                    **{
                        field: metrics[field] - baseline_metrics[field]
                        for field in COMPARABLE_METRIC_FIELDS
                    },
                },
            }
        )

    return {
        "baseline_profile": "full",
        "case_ids": case_ids,
        "profiles": profiles,
    }
