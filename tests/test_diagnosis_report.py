"""诊断报告与证据引用校验的验收测试。"""

import pytest

from app.harness.report import DiagnosisReportValidator
from app.models.contracts import DiagnosisReport, EvidenceItem


def evidence(identifier: str) -> EvidenceItem:
    """构造稳定 ID 的最小证据。"""
    return EvidenceItem(evidence_id=identifier * 64, tool_name="query_metrics", content="{}")


def report(*references: str) -> DiagnosisReport:
    """构造包含证据引用的最小诊断报告。"""
    return DiagnosisReport(
        summary="支付服务延迟升高。",
        probable_root_cause="数据库连接池耗尽。",
        confidence=0.8,
        evidence_ids=[reference * 64 for reference in references],
        recommended_actions=["检查连接池上限。"],
    )


def test_validator_accepts_report_referencing_known_evidence() -> None:
    """报告只能引用当前运行中存在的证据。"""
    validator = DiagnosisReportValidator()
    validator.validate(report("a"), [evidence("a")])


def test_validator_rejects_unknown_and_duplicate_references() -> None:
    """未知或重复证据引用不能进入最终报告。"""
    validator = DiagnosisReportValidator()

    with pytest.raises(ValueError, match="unknown evidence"):
        validator.validate(report("b"), [evidence("a")])
    with pytest.raises(ValueError, match="duplicate evidence"):
        validator.validate(report("a", "a"), [evidence("a")])
