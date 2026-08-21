"""跨 Harness 测试共享的结构化诊断报告构造函数。"""

from typing import Any

from app.harness.evidence import EvidenceCollector
from app.models.contracts import DiagnosisReport


def diagnosis_report(*, evidence_ids: list[str] | None = None) -> DiagnosisReport:
    """构造满足 final_answer 契约的最小诊断报告。"""
    return DiagnosisReport(
        summary="支付服务延迟升高。",
        probable_root_cause="数据库连接池耗尽。",
        confidence=0.8,
        evidence_ids=evidence_ids or ["a" * 64],
        recommended_actions=["检查数据库连接池上限。"],
    )


def report_for_observation(*, tool_name: str, observation: dict[str, Any]) -> DiagnosisReport:
    """构造引用确定性工具观察结果的诊断报告。"""
    evidence = EvidenceCollector().collect(tool_name=tool_name, observation=observation)
    return diagnosis_report(evidence_ids=[evidence.evidence_id])
