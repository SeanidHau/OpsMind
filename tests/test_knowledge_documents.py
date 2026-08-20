"""Markdown 知识文档加载与切分的验收测试。"""

from pathlib import Path

import pytest

from app.models.contracts import KnowledgeDocument
from app.rag.documents import MarkdownChunker, MarkdownKnowledgeLoader


def write_runbook(path: Path) -> None:
    """写入具有 Front Matter 和标题的最小运维 Runbook。"""
    path.write_text(
        """---
service: payment-service
document_type: runbook
severity: P1
---
# 支付服务超时排查

先检查数据库连接池使用率，再确认错误率和 P95 延迟。
连接池耗尽时，支付请求通常出现超时。
""",
        encoding="utf-8",
    )


def test_loader_extracts_front_matter_and_markdown_body(tmp_path: Path) -> None:
    """加载器应保留业务元数据，且不把 Front Matter 混入正文。"""
    path = tmp_path / "payment-runbook.md"
    write_runbook(path)

    document = MarkdownKnowledgeLoader().load(path)

    assert document.source_id == "payment-runbook"
    assert document.metadata == {
        "service": "payment-service",
        "document_type": "runbook",
        "severity": "P1",
        "title": "支付服务超时排查",
    }
    assert "service: payment-service" not in document.content
    assert "连接池耗尽" in document.content


def test_chunker_generates_stable_overlapping_chunks() -> None:
    """同一输入重复切分时，分块内容、顺序和 ID 必须完全一致。"""
    document = KnowledgeDocument(
        source_id="payment-runbook",
        content="abcdefghijklmnopqrstuvwxyz",
        metadata={"service": "payment-service"},
    )
    chunker = MarkdownChunker(chunk_size=10, chunk_overlap=2)

    first = chunker.split(document)
    second = chunker.split(document)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert first[0].content[-2:] == first[1].content[:2]
    assert [chunk.index for chunk in first] == [0, 1, 2]


def test_chunker_exports_langchain_documents_with_traceable_metadata() -> None:
    """向量和关键词检索层应获得带来源和分块 ID 的 LangChain 文档。"""
    document = KnowledgeDocument(
        source_id="payment-runbook",
        content="数据库连接池耗尽会导致支付请求超时。",
        metadata={"service": "payment-service", "document_type": "runbook"},
    )
    chunks = MarkdownChunker(chunk_size=100, chunk_overlap=0).split(document)

    langchain_documents = MarkdownChunker.to_langchain_documents(chunks)

    assert langchain_documents[0].page_content == chunks[0].content
    assert langchain_documents[0].metadata["source_id"] == "payment-runbook"
    assert langchain_documents[0].metadata["chunk_id"] == chunks[0].chunk_id
    assert langchain_documents[0].metadata["service"] == "payment-service"


def test_chunker_rejects_invalid_window_configuration() -> None:
    """无效切分窗口必须在启动时失败，避免无穷循环或空分块。"""
    with pytest.raises(ValueError, match="chunk_size"):
        MarkdownChunker(chunk_size=0)

    with pytest.raises(ValueError, match="chunk_overlap"):
        MarkdownChunker(chunk_size=10, chunk_overlap=10)
