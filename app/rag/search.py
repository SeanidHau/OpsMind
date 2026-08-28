"""查询向量化与 Milvus 检索入口。"""

from __future__ import annotations

from typing import Protocol

from app.models.contracts import RetrievalHit
from app.rag.embeddings import EmbeddingClient


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

    def close(self) -> None:
        """关闭底层资源。"""


class KnowledgeSearcher:
    """将自然语言查询转换为向量并检索持久化知识。"""

    def __init__(self, *, embedder: EmbeddingClient, vector_store: SearchableVectorStore) -> None:
        """注入同一 Embedding 模型和对应维度的向量存储。"""
        self._embedder = embedder
        self._vector_store = vector_store

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        """验证查询后生成查询向量，并执行元数据过滤检索。"""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be blank")
        return self._vector_store.search(
            self._embedder.embed_query(normalized_query),
            top_k=top_k,
            metadata_filter=metadata_filter,
        )

    def close(self) -> None:
        """释放底层 Milvus 客户端连接。"""
        self._vector_store.close()
