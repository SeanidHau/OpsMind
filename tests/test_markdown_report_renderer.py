"""Markdown 诊断报告渲染的验收测试。"""

import pytest

from app.harness.report_renderer import MarkdownReportRenderer
from app.models.contracts import DiagnosisReport, EvidenceItem


def evidence(identifier: str, content: str) -> EvidenceItem:
    """构造带有稳定 ID 的最小证据。"""
    return EvidenceItem(
        evidence_id=identifier * 64,
        tool_name="query_metrics",
        content=content,
    )


def report(*references: str) -> DiagnosisReport:
    """构造最小的可渲染诊断报告。"""
    return DiagnosisReport(
        summary="支付服务延迟升高。",
        probable_root_cause="数据库连接池耗尽。",
        confidence=0.8,
        evidence_ids=[reference * 64 for reference in references],
        recommended_actions=["检查连接池上限。"],
    )


def test_renderer_outputs_fixed_sections_and_cited_evidence() -> None:
    """渲染结果只包含报告实际引用的证据。"""
    result = MarkdownReportRenderer().render(
        report("a"),
        [evidence("a", "连接池已用尽"), evidence("b", "不应出现在报告中")],
    )

    assert "# OpsMind 诊断报告" in result
    assert "## 候选原因" in result
    assert "80%" in result
    assert "`query_metrics`：连接池已用尽" in result
    assert "检查连接池上限。" in result
    assert "不应出现在报告中" not in result


def test_renderer_rejects_unknown_evidence_reference() -> None:
    """未知引用必须在渲染前被校验器拒绝。"""
    with pytest.raises(ValueError, match="unknown evidence"):
        MarkdownReportRenderer().render(report("b"), [evidence("a", "连接池已用尽")])


def test_renderer_lists_deduplicated_sources_from_cited_knowledge_evidence() -> None:
    """知识检索证据应在报告中显示来源，不解析未引用或无效 JSON。"""
    knowledge_evidence = EvidenceItem(
        evidence_id="a" * 64,
        tool_name="query_knowledge",
        content=(
            '{"hits":[{"source_id":"payment-runbook"},'
            '{"source_id":"payment-runbook"},{"source_id":"postgres-guide"}]}'
        ),
    )

    result = MarkdownReportRenderer().render(report("a"), [knowledge_evidence])

    assert "## 知识来源" in result
    assert result.count("`payment-runbook`") == 1
    assert "- `postgres-guide`" in result
