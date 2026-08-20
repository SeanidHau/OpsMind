"""确定性的内存 BM25 关键词检索。"""

from __future__ import annotations

import re
from collections import Counter
from math import log

from app.models.contracts import KnowledgeChunk, RetrievalHit


class BM25Retriever:
    """对稳定知识分块执行关键词检索与元数据过滤。"""

    _TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")

    def __init__(
        self,
        chunks: list[KnowledgeChunk],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """建立内存词频索引。"""
        if k1 <= 0:
            raise ValueError("k1 must be greater than 0")
        if not 0 <= b <= 1:
            raise ValueError("b must be between 0 and 1")

        self._chunks = tuple(chunks)
        self._k1 = k1
        self._b = b
        self._token_counts: tuple[Counter[str], ...] = tuple(
            Counter(self._tokenize(chunk.content)) for chunk in self._chunks
        )
        self._document_lengths = tuple(
            sum(token_counts.values()) for token_counts in self._token_counts
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        """按 BM25 分数返回匹配元数据过滤条件的知识分块。"""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            raise ValueError("query must contain at least one searchable token")
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")

        candidate_indices = self._candidate_indices(metadata_filter)
        if not candidate_indices:
            return []

        average_length = sum(self._document_lengths[index] for index in candidate_indices) / len(
            candidate_indices
        )
        scores = {index: 0.0 for index in candidate_indices}

        # 同一查询词只贡献一次，避免重复输入无意义放大分数。
        for term in set(query_tokens):
            document_frequency = sum(
                term in self._token_counts[index] for index in candidate_indices
            )
            if document_frequency == 0:
                continue

            corpus_size = len(candidate_indices)
            inverse_document_frequency = log(
                1 + (corpus_size - document_frequency + 0.5) / (document_frequency + 0.5)
            )

            for index in candidate_indices:
                term_frequency = self._token_counts[index][term]
                if term_frequency == 0:
                    continue

                document_length = self._document_lengths[index]
                denominator = term_frequency + self._k1 * (
                    1 - self._b + self._b * document_length / average_length
                )
                scores[index] += inverse_document_frequency * (
                    term_frequency * (self._k1 + 1) / denominator
                )

        ranked = sorted(
            ((index, score) for index, score in scores.items() if score > 0),
            key=lambda item: (-item[1], self._chunks[item[0]].chunk_id),
        )

        return [
            RetrievalHit(
                chunk=self._chunks[index],
                score=score,
                rank=rank,
            )
            for rank, (index, score) in enumerate(ranked[:top_k], start=1)
        ]

    def _candidate_indices(
        self,
        metadata_filter: dict[str, str] | None,
    ) -> list[int]:
        """返回满足所有元数据约束的候选分块索引。"""
        if not metadata_filter:
            return list(range(len(self._chunks)))

        return [
            index
            for index, chunk in enumerate(self._chunks)
            if all(chunk.metadata.get(key) == value for key, value in metadata_filter.items())
        ]

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        """将英文词、数字和中文单字切分为稳定词元。"""
        return cls._TOKEN_PATTERN.findall(text.casefold())
