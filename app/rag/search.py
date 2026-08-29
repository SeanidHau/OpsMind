"""查询向量化与 Milvus 检索入口。"""

from __future__ import annotations

from typing import Protocol

from app.models.contracts import (
    FusedRetrievalHit,
    KnowledgeChunk,
    KnowledgeDocument,
    RetrievalHit,
    VectorizedChunk,
)
from app.rag.bm25 import BM25Retriever
from app.rag.embeddings import EmbeddingClient
from app.rag.hybrid import HybridRetriever
from app.rag.ingestion import KnowledgeIngestor


class SearchableVectorStore(Protocol):
    """查询侧需要的最小向量存储接口。"""

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        """按向量查询知识分块。"""

    def upsert(self, records: list[VectorizedChunk]) -> None:
        """幂等保存已向量化的知识分块。"""

    def close(self) -> None:
        """关闭底层资源。"""


class KnowledgeSearcher:
    """将自然语言查询转换为向量，并融合关键词与持久化向量检索。"""

    def __init__(
        self,
        *,
        embedder: EmbeddingClient,
        vector_store: SearchableVectorStore,
        keyword_retriever: BM25Retriever | None = None,
    ) -> None:
        """注入 Embedding、向量存储和可选的同源 BM25 索引。"""
        self._embedder = embedder
        self._vector_store = vector_store
        self._hybrid_retriever = (
            HybridRetriever(
                keyword_retriever=keyword_retriever,
                vector_retriever=vector_store,
            )
            if keyword_retriever is not None
            else None
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[FusedRetrievalHit]:
        """验证查询后生成向量，并执行可用的混合检索。"""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be blank")
        query_vector = self._embedder.embed_query(normalized_query)
        if self._hybrid_retriever is not None:
            return self._hybrid_retriever.search(
                query=normalized_query,
                query_vector=query_vector,
                top_k=top_k,
                metadata_filter=metadata_filter,
            )

        return [
            FusedRetrievalHit(
                chunk=hit.chunk,
                score=hit.score,
                rank=hit.rank,
                retriever_names=["vector"],
            )
            for hit in self._vector_store.search(
                query_vector,
                top_k=top_k,
                metadata_filter=metadata_filter,
            )
        ]

    def close(self) -> None:
        """释放底层 Milvus 客户端连接。"""
        self._vector_store.close()

    def ingest_document(
        self, document: KnowledgeDocument, *, all_chunks: list[KnowledgeChunk]
    ) -> list[VectorizedChunk]:
        """将新增 Markdown 写入向量库，并立即刷新关键词索引。"""
        records = KnowledgeIngestor(
            embedder=self._embedder, vector_store=self._vector_store
        ).ingest_document(document)
        self._hybrid_retriever = HybridRetriever(
            keyword_retriever=BM25Retriever(all_chunks), vector_retriever=self._vector_store
        )
        return records
