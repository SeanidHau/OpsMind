"""统一组合关键词、向量与 RRF 的混合检索入口。"""

from __future__ import annotations

from typing import Protocol

from app.models.contracts import FusedRetrievalHit, RetrievalHit
from app.rag.bm25 import BM25Retriever
from app.rag.fusion import ReciprocalRankFusion, RetrieverResult


class VectorRetriever(Protocol):
    """混合检索所需的最小向量召回接口。"""

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        """返回已按向量相关性排序的知识分块。"""


class HybridRetriever:
    """执行双路检索，并返回带来源的融合证据。"""

    def __init__(
        self,
        *,
        keyword_retriever: BM25Retriever,
        vector_retriever: VectorRetriever,
        fusion: ReciprocalRankFusion | None = None,
    ) -> None:
        """注入两条检索路径，并允许替换 RRF 配置。"""
        self._keyword_retriever = keyword_retriever
        self._vector_retriever = vector_retriever
        self._fusion = fusion or ReciprocalRankFusion()

    def search(
        self,
        *,
        query: str,
        query_vector: list[float],
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[FusedRetrievalHit]:
        """执行关键词与向量检索，再融合两条结果。"""
        if top_k <= 0:
            raise ValueError("`top_k` must be greater than 0")

        # 两条路径共享过滤条件和返回上限，避免候选范围不一致。
        keyword_hits = self._keyword_retriever.search(
            query,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )
        vector_hits = self._vector_retriever.search(
            query_vector,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )

        # RRF 只处理各检索器的排名，不直接比较不同检索器的原始分数。
        return self._fusion.fuse(
            [
                RetrieverResult(name="bm25", hits=keyword_hits),
                RetrieverResult(name="vector", hits=vector_hits),
            ],
            top_k=top_k,
        )
