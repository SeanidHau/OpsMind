"""InMemoryVectorRetriever 的验收测试。"""

import pytest

from app.models.contracts import KnowledgeChunk, VectorizedChunk
from app.rag.vector import InMemoryVectorRetriever


def make_records() -> list[VectorizedChunk]:
    """构造维度一致、来自不同服务的固定向量。"""
    return [
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="payment-db",
                source_id="payment-runbook",
                index=0,
                content="支付服务数据库连接池耗尽。",
                metadata={"service": "payment-service"},
            ),
            vector=[1.0, 0.0],
        ),
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="payment-cache",
                source_id="payment-cache-guide",
                index=0,
                content="支付服务缓存命中率下降。",
                metadata={"service": "payment-service"},
            ),
            vector=[0.6, 0.8],
        ),
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="order-db",
                source_id="order-runbook",
                index=0,
                content="订单服务数据库慢查询。",
                metadata={"service": "order-service"},
            ),
            vector=[-1.0, 0.0],
        ),
    ]


def test_vector_retriever_returns_cosine_ranked_hits() -> None:
    """查询向量应按余弦相似度返回正相关分块。"""
    hits = InMemoryVectorRetriever(make_records()).search([1.0, 0.0], top_k=2)

    assert [hit.chunk.chunk_id for hit in hits] == ["payment-db", "payment-cache"]
    assert [hit.rank for hit in hits] == [1, 2]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].score > hits[1].score


def test_metadata_filter_applies_before_vector_ranking() -> None:
    """元数据过滤应只在目标服务的候选分块内执行排序。"""
    hits = InMemoryVectorRetriever(make_records()).search(
        [1.0, 0.0],
        metadata_filter={"service": "order-service"},
    )

    assert hits == []


def test_vector_retriever_tie_breaker_is_stable_by_chunk_id() -> None:
    """相同相似度的结果必须按 chunk_id 排序。"""
    records = [
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="b",
                source_id="source-b",
                index=0,
                content="分块 b",
            ),
            vector=[1.0, 0.0],
        ),
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="a",
                source_id="source-a",
                index=0,
                content="分块 a",
            ),
            vector=[1.0, 0.0],
        ),
    ]

    hits = InMemoryVectorRetriever(records).search([1.0, 0.0])

    assert [hit.chunk.chunk_id for hit in hits] == ["a", "b"]


def test_vector_retriever_rejects_invalid_vectors_and_parameters() -> None:
    """零向量、维度不一致和非正 Top-K 必须显式拒绝。"""
    with pytest.raises(ValueError, match="dimension"):
        InMemoryVectorRetriever(
            [
                VectorizedChunk(
                    chunk=KnowledgeChunk(
                        chunk_id="a",
                        source_id="source-a",
                        index=0,
                        content="分块 a",
                    ),
                    vector=[1.0, 0.0],
                ),
                VectorizedChunk(
                    chunk=KnowledgeChunk(
                        chunk_id="b",
                        source_id="source-b",
                        index=0,
                        content="分块 b",
                    ),
                    vector=[1.0],
                ),
            ]
        )

    retriever = InMemoryVectorRetriever(make_records())
    with pytest.raises(ValueError, match="zero"):
        retriever.search([0.0, 0.0])
    with pytest.raises(ValueError, match="dimension"):
        retriever.search([1.0])
    with pytest.raises(ValueError, match="top_k"):
        retriever.search([1.0, 0.0], top_k=0)
