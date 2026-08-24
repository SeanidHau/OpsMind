"""已归档 Harness 轨迹的确定性离线评测。"""

from __future__ import annotations

from app.harness.report import DiagnosisReportValidator
from app.models.contracts import (
    BudgetState,
    DiagnosisReport,
    EvaluationCheck,
    EventType,
    EvidenceItem,
    HarnessStatus,
    RunSnapshot,
    TrajectoryEvaluation,
)


class TrajectoryEvaluator:
    """检查运行终止、checkpoint、预算和完成报告的基本不变量。"""

    def __init__(self, *, report_validator: DiagnosisReportValidator | None = None) -> None:
        """注入报告校验器，确保评测复用与最终输出一致的引用规则。"""
        self._report_validator = report_validator or DiagnosisReportValidator()

    def evaluate(self, snapshot: RunSnapshot) -> TrajectoryEvaluation:
        """对单份归档快照执行确定性检查，不调用模型或工具。"""
        checks = [
            self._check_checkpoint(snapshot),
            self._check_terminal_event(snapshot),
            self._check_budget(snapshot),
            self._check_completed_report(snapshot),
        ]
        score = sum(check.passed for check in checks) / len(checks)

        return TrajectoryEvaluation(
            run_id=snapshot.run_id,
            passed=all(check.passed for check in checks),
            score=score,
            checks=checks,
        )

    @staticmethod
    def _check_checkpoint(snapshot: RunSnapshot) -> EvaluationCheck:
        """确认快照在归档前已记录 checkpoint 事件。"""
        passed = bool(snapshot.trajectory) and (
            snapshot.trajectory[-1].event_type is EventType.CHECKPOINT_SAVED
        )
        return EvaluationCheck(
            name="checkpoint_saved",
            passed=passed,
            detail="轨迹以 CHECKPOINT_SAVED 结束。"
            if passed
            else "归档轨迹缺少末尾 CHECKPOINT_SAVED 事件。",
        )

    @staticmethod
    def _check_terminal_event(snapshot: RunSnapshot) -> EvaluationCheck:
        """确认终止状态与业务终止事件相匹配。"""
        expected_events = {
            HarnessStatus.COMPLETED: {EventType.RUN_COMPLETED},
            HarnessStatus.WAITING_APPROVAL: {EventType.RUN_PAUSED},
            HarnessStatus.FAILED: {EventType.RUN_FAILED},
            HarnessStatus.BLOCKED: {EventType.ACTION_BLOCKED, EventType.VERIFICATION_FAILED},
            HarnessStatus.STALLED: {EventType.VERIFICATION_FAILED},
        }
        expected = (
            expected_events.get(snapshot.terminal_status)
            if snapshot.terminal_status is not None
            else None
        )
        business_events = {
            event.event_type
            for event in snapshot.trajectory
            if event.event_type is not EventType.CHECKPOINT_SAVED
        }
        passed = expected is not None and bool(expected & business_events)

        return EvaluationCheck(
            name="terminal_event",
            passed=passed,
            detail="终止状态存在对应业务事件。" if passed else "终止状态缺少对应的业务终止事件。",
        )

    @staticmethod
    def _check_budget(snapshot: RunSnapshot) -> EvaluationCheck:
        """确认保存的预算状态仍满足全部上限。"""
        try:
            BudgetState.model_validate(snapshot.final_state["budget"])
        except (KeyError, ValueError, TypeError) as error:
            return EvaluationCheck(
                name="budget_within_limits",
                passed=False,
                detail=f"预算状态无效：{error}",
            )

        return EvaluationCheck(
            name="budget_within_limits",
            passed=True,
            detail="已用预算未超过配置上限。",
        )

    def _check_completed_report(self, snapshot: RunSnapshot) -> EvaluationCheck:
        """已完成运行必须含有可验证、可追溯的结构化报告。"""
        if snapshot.terminal_status is not HarnessStatus.COMPLETED:
            return EvaluationCheck(
                name="completed_report_evidence",
                passed=True,
                detail="运行未完成，不适用最终报告检查。",
            )

        try:
            report = DiagnosisReport.model_validate(snapshot.final_state["diagnosis_report"])
            evidence = [
                EvidenceItem.model_validate(item) for item in snapshot.final_state["evidence"]
            ]
            self._report_validator.validate(report, evidence)
        except (KeyError, TypeError, ValueError) as error:
            return EvaluationCheck(
                name="completed_report_evidence",
                passed=False,
                detail=f"完成报告不可验证：{error}",
            )

        return EvaluationCheck(
            name="completed_report_evidence",
            passed=True,
            detail="完成报告的证据引用均可追溯。",
        )
