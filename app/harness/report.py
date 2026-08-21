"""结构化诊断报告的证据引用校验。"""

from app.models.contracts import DiagnosisReport, EvidenceItem


class DiagnosisReportValidator:
    """确保报告中的每个引用都可追溯到当前运行证据。"""

    def validate(
        self,
        report: DiagnosisReport,
        evidence: list[EvidenceItem],
    ) -> None:
        """拒绝重复或不存在的证据 ID。"""
        if len(set(report.evidence_ids)) != len(report.evidence_ids):
            raise ValueError("duplicate evidence references are not allowed")

        known_ids = {item.evidence_id for item in evidence}
        unknown_ids = sorted(set(report.evidence_ids) - known_ids)
        if unknown_ids:
            raise ValueError(f"unknown evidence references: {', '.join(unknown_ids)}")
