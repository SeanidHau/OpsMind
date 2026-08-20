"""可复现的内存向量检索。"""

from __future__ import annotations

from math import sqrt

from app.models.contracts import RetrievalHit, VectorizedChunk


class InMemoryVectorRetriever:
    """使用余弦相似度检索预计算的知识分块向量。"""

    def __init__(self, records: list[VectorizedChunk]) -> None:
        """建立内存向量索引，并验证维度和向量范数。"""
        self._records = tuple(records)
        self._dimension: int | None = None
        self._norms: tuple[float, ...] = ()

        if not self._records:
            return

        self._dimension = len(self._records[0].vector)
        if any(len(record.vector) != self._dimension for record in self._records):
            raise ValueError("all vectors must have the same dimension")

        norms = tuple(self._norm(record.vector) for record in self._records)
        if any(norm == 0 for norm in norms):
            raise ValueError("sorted vectors must not be zero vectors")

        self._norms = norms

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        """按余弦相似度返回满足元数据过滤条件的分块。"""
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")

        query_norm = self._norm(query_vector)
        if query_norm == 0:
            raise ValueError("query vector must be a zero vector")

        if not self._records:
            return []

        if len(query_vector) != self._dimension:
            raise ValueError("query vector dimension does not match the index")

        scored_records: list[tuple[VectorizedChunk, float]] = []
        for record, record_norm in zip(self._records, self._norms, strict=True):
            if metadata_filter and any(
                record.chunk.metadata.get(key) != value for key, value in metadata_filter.items()
            ):
                continue

            # 余弦相似度只比较方向，避免向量长度直接影响排名。
            score = self._dot(query_vector, record.vector) / (query_norm * record_norm)
            if score > 0:
                scored_records.append((record, score))

        ranked = sorted(
            scored_records,
            key=lambda item: (-item[1], item[0].chunk.chunk_id),
        )

        return [
            RetrievalHit(chunk=record.chunk, score=score, rank=rank)
            for rank, (record, score) in enumerate(ranked[:top_k], start=1)
        ]

    @staticmethod
    def _dot(left: list[float], right: list[float]) -> float:
        """计算两个同维向量的点积。"""
        return sum(
            left_value * right_value for left_value, right_value in zip(left, right, strict=True)
        )

    @staticmethod
    def _norm(vector: list[float]) -> float:
        """计算向量的欧几里得范数。"""
        return sqrt(sum(value * value for value in vector))
