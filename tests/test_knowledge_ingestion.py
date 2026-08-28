"""KnowledgeIngestor 的验收测试。"""

from pathlib import Path

import pytest

from app.models.contracts import KnowledgeDocument, VectorizedChunk
from app.rag.documents import MarkdownChunker
from app.rag.ingestion import KnowledgeIngestor


class RecordingEmbedder:
    """记录入参并返回与文本顺序一致的确定性向量。"""

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.texts: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.texts = texts
        return self.vectors


class RecordingVectorStore:
    """记录写入的向量分块，避免单元测试依赖 Milvus。"""

    def __init__(self) -> None:
        self.records: list[VectorizedChunk] = []

    def upsert(self, records: list[VectorizedChunk]) -> None:
        self.records = records


def test_ingestor_loads_markdown_and_preserves_chunk_vector_alignment(tmp_path: Path) -> None:
    """Markdown 元数据、分块顺序和 embedding 输出必须一起写入存储。"""
    path = tmp_path / "payment-runbook.md"
    path.write_text(
        "---\nservice: payment-service\n---\n# 支付超时\n\n检查数据库连接池。",
        encoding="utf-8",
    )
    embedder = RecordingEmbedder([[1.0, 0.0]])
    store = RecordingVectorStore()

    records = KnowledgeIngestor(embedder=embedder, vector_store=store).ingest_markdown(path)

    assert embedder.texts == ["# 支付超时\n\n检查数据库连接池。"]
    assert records == store.records
    assert records[0].chunk.source_id == "payment-runbook"
    assert records[0].chunk.metadata == {"service": "payment-service", "title": "支付超时"}
    assert records[0].vector == [1.0, 0.0]


def test_ingestor_rejects_embedding_count_mismatch_before_writing() -> None:
    """Embedding 缺失时不得向存储写入部分分块。"""
    store = RecordingVectorStore()
    ingestor = KnowledgeIngestor(
        embedder=RecordingEmbedder([[1.0, 0.0]]),
        vector_store=store,
        chunker=MarkdownChunker(chunk_size=4, chunk_overlap=0),
    )

    with pytest.raises(ValueError, match="one vector"):
        ingestor.ingest_document(KnowledgeDocument(source_id="source", content="abcdefgh"))

    assert store.records == []
