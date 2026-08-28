"""ReciprocalRankFusion 的验收测试。"""

import pytest

from app.models.contracts import KnowledgeChunk, RetrievalHit
from app.rag.fusion import ReciprocalRankFusion, RetrieverResult


def make_hit(chunk_id: str, rank: int) -> RetrievalHit:
    """构造用于融合排序的最小检索命中。"""
    return RetrievalHit(
        chunk=KnowledgeChunk(
            chunk_id=chunk_id,
            source_id=f"source-{chunk_id}",
            index=0,
            content=f"知识分块 {chunk_id}",
        ),
        score=1.0,
        rank=rank,
    )


def test_rrf_rewards_chunks_confirmed_by_multiple_retrievers() -> None:
    """被多个检索器命中的分块应优先于单一路径的命中。"""
    fused = ReciprocalRankFusion().fuse(
        [
            RetrieverResult(name="bm25", hits=[make_hit("shared", 1), make_hit("solo", 2)]),
            RetrieverResult(name="vector", hits=[make_hit("shared", 2)]),
        ]
    )

    assert [hit.chunk.chunk_id for hit in fused] == ["shared", "solo"]
    assert fused[0].retriever_names == ["bm25", "vector"]
    assert fused[0].score > fused[1].score
    assert [hit.rank for hit in fused] == [1, 2]


def test_rrf_deduplicates_repeated_chunks_from_one_retriever() -> None:
    """同一检索器重复返回分块时，只使用其最高排名的一次贡献。"""
    fused = ReciprocalRankFusion(rank_constant=10).fuse(
        [
            RetrieverResult(
                name="bm25",
                hits=[make_hit("repeat", 1), make_hit("repeat", 3)],
            )
        ]
    )

    assert len(fused) == 1
    assert fused[0].score == pytest.approx(1 / 11)
    assert fused[0].retriever_names == ["bm25"]


def test_rrf_tie_breaker_is_stable_by_chunk_id() -> None:
    """总分相同的结果必须按 chunk_id 排序，避免评测顺序波动。"""
    fused = ReciprocalRankFusion().fuse(
        [
            RetrieverResult(name="bm25", hits=[make_hit("b", 1)]),
            RetrieverResult(name="vector", hits=[make_hit("a", 1)]),
        ]
    )

    assert [hit.chunk.chunk_id for hit in fused] == ["a", "b"]


def test_rrf_rejects_invalid_configuration_and_input() -> None:
    """无效配置、重复检索器名称与非正 Top-K 不应静默执行。"""
    with pytest.raises(ValueError, match="rank_constant"):
        ReciprocalRankFusion(rank_constant=0)

    fusion = ReciprocalRankFusion()
    with pytest.raises(ValueError, match="top_k"):
        fusion.fuse([], top_k=0)

    with pytest.raises(ValueError, match="unique"):
        fusion.fuse(
            [
                RetrieverResult(name="bm25", hits=[]),
                RetrieverResult(name="bm25", hits=[]),
            ]
        )
