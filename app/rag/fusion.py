"""确定性的 Reciprocal Rank Fusion 实现。"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.contracts import FusedRetrievalHit, KnowledgeChunk, RetrievalHit


@dataclass(frozen=True, slots=True)
class RetrieverResult:
    """一个检索器返回的已排序命中列表。"""

    name: str
    hits: list[RetrievalHit]


class ReciprocalRankFusion:
    """合并多个检索器的排序结果，并生成稳定的融合排名。"""

    def __init__(self, *, rank_constant: int = 60) -> None:
        """保存 RRF 的排名平滑变量。"""
        if rank_constant <= 0:
            raise ValueError("rank_constant must be greater than 0")

        self._rank_constant = rank_constant

    def fuse(
        self,
        results: list[RetrieverResult],
        *,
        top_k: int = 3,
    ) -> list[FusedRetrievalHit]:
        """按 RRF 分数融合多个检索器的结果。"""
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")

        retriever_names = [result.name for result in results]
        if any(not name.strip() for name in retriever_names):
            raise ValueError("retriever_names must not be blank")
        if len(retriever_names) != len(set(retriever_names)):
            raise ValueError("retriever names must be unique")

        scores: dict[str, float] = {}
        chunks: dict[str, KnowledgeChunk] = {}
        contributing_retrievers: dict[str, list[str]] = {}

        for result in results:
            # 同一检索器内的重复分块只保留排名最高的一次。
            best_hits: dict[str, RetrievalHit] = {}
            for hit in result.hits:
                current = best_hits.get(hit.chunk.chunk_id)
                if current is None or hit.rank < current.rank:
                    best_hits[hit.chunk.chunk_id] = hit

            for chunk_id, hit in best_hits.items():
                # RRF 只依赖排名，避免不同检索器的原始分数不可比。
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (self._rank_constant + hit.rank)
                chunks.setdefault(chunk_id, hit.chunk)
                contributing_retrievers.setdefault(chunk_id, []).append(result.name)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))

        return [
            FusedRetrievalHit(
                chunk=chunks[chunk_id],
                score=score,
                rank=rank,
                retriever_names=contributing_retrievers[chunk_id],
            )
            for rank, (chunk_id, score) in enumerate(ranked[:top_k], start=1)
        ]
