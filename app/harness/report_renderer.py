"""将已校验的诊断报告渲染为可阅读、可追溯的 Markdown。"""

from app.harness.report import DiagnosisReportValidator
from app.models.contracts import DiagnosisReport, EvidenceItem


class MarkdownReportRenderer:
    """把结构化报告和证据列表转换为面向运维人员的 Markdown。"""

    def __init__(self, *, validator: DiagnosisReportValidator | None = None) -> None:
        """注入引用校验器，确保渲染前先验证报告可信边界。"""
        self._validator = validator or DiagnosisReportValidator()

    def render(
        self,
        report: DiagnosisReport,
        evidence: list[EvidenceItem],
    ) -> str:
        """校验引用后输出固定结构的诊断报告。"""
        self._validator.validate(report, evidence)
        evidence_by_id = {item.evidence_id: item for item in evidence}

        # 只展示报告实际引用的证据，避免未使用的观察结果混入结论。
        citation_lines = [
            f"- `{evidence_id}` · `{evidence_by_id[evidence_id].tool_name}`："
            f"{evidence_by_id[evidence_id].content}"
            for evidence_id in report.evidence_ids
        ]
        action_lines = [f"- {action}" for action in report.recommended_actions]

        return "\n".join(
            [
                "# OpsMind 诊断报告",
                "",
                "## 摘要",
                report.summary,
                "",
                "## 候选原因",
                report.probable_root_cause,
                "",
                "## 置信度",
                f"{report.confidence:.0%}",
                "",
                "## 证据引用",
                *citation_lines,
                "",
                "## 建议操作",
                *action_lines,
            ]
        )
