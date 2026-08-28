"""知识文档的切分、向量化与持久化入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.models.contracts import KnowledgeDocument, VectorizedChunk
from app.rag.documents import MarkdownChunker, MarkdownKnowledgeLoader


class DocumentEmbedder(Protocol):
    """兼容 LangChain `Embeddings.embed_documents` 的最小接口。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为每个文本返回一个同序向量。"""


class VectorStore(Protocol):
    """知识入库所需的最小向量存储接口。"""

    def upsert(self, records: list[VectorizedChunk]) -> None:
        """幂等保存已向量化的知识分块。"""


class KnowledgeIngestor:
    """将 Markdown 知识文档转换为 Milvus 可保存的向量分块。"""

    def __init__(
        self,
        *,
        embedder: DocumentEmbedder,
        vector_store: VectorStore,
        chunker: MarkdownChunker | None = None,
    ) -> None:
        """注入嵌入客户端、目标存储和可选的分块配置。"""
        self._embedder = embedder
        self._vector_store = vector_store
        self._chunker = chunker or MarkdownChunker()

    def ingest_markdown(self, path: Path) -> list[VectorizedChunk]:
        """加载一个 Markdown 文件并写入其全部稳定分块。"""
        return self.ingest_document(MarkdownKnowledgeLoader().load(path))

    def ingest_document(self, document: KnowledgeDocument) -> list[VectorizedChunk]:
        """切分、向量化并在向量数量正确时一次写入目标存储。"""
        chunks = self._chunker.split(document)
        vectors = self._embedder.embed_documents([chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("embedder must return one vector for every chunk")

        records = [
            VectorizedChunk(chunk=chunk, vector=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._vector_store.upsert(records)
        return records
