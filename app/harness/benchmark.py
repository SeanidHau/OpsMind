"""基于运行快照的可重复离线 benchmark。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from app.harness.evaluation import TrajectoryEvaluator
from app.harness.snapshot import RunSnapshotFactory
from app.models.contracts import (
    BenchmarkCaseResult,
    BenchmarkResult,
    DiagnosisState,
    EvaluationCase,
    EvaluationCheck,
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

        case_results = [
            self._evaluate_case(case=case, snapshot=await subject.run_case(case)) for case in cases
        ]
        score = sum(result.score for result in case_results) / len(case_results)

        return BenchmarkResult(
            passed=all(result.passed for result in case_results),
            score=score,
            case_results=case_results,
        )

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
