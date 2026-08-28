"""将已校验的诊断报告渲染为可阅读、可追溯的 Markdown。"""

import json
from collections.abc import Mapping

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
        cited_evidence = [evidence_by_id[evidence_id] for evidence_id in report.evidence_ids]
        knowledge_sources = self._knowledge_sources(cited_evidence)
        action_lines = [f"- {action}" for action in report.recommended_actions]

        lines = [
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
        ]
        if knowledge_sources:
            lines.extend(
                [
                    "",
                    "## 知识来源",
                    *(f"- `{source_id}`" for source_id in knowledge_sources),
                ]
            )
        lines.extend(["", "## 建议操作", *action_lines])
        return "\n".join(lines)

    @staticmethod
    def _knowledge_sources(evidence: list[EvidenceItem]) -> list[str]:
        """从已引用的知识检索证据提取稳定来源标识。"""
        source_ids: list[str] = []
        for item in evidence:
            if item.tool_name != "query_knowledge":
                continue
            try:
                payload = json.loads(item.content)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, Mapping) or not isinstance(payload.get("hits"), list):
                continue
            for hit in payload["hits"]:
                if not isinstance(hit, Mapping):
                    continue
                source_id = hit.get("source_id")
                if isinstance(source_id, str) and source_id.strip() and source_id not in source_ids:
                    source_ids.append(source_id)
        return source_ids
