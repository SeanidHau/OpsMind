"""BM25Retriever 的验收测试。"""

import pytest

from app.models.contracts import KnowledgeChunk
from app.rag.bm25 import BM25Retriever


def make_chunks() -> list[KnowledgeChunk]:
    """构造来自不同服务的确定性知识分块。"""
    return [
        KnowledgeChunk(
            chunk_id="payment-db",
            source_id="payment-runbook",
            index=0,
            content="数据库连接池耗尽会导致支付请求超时和错误率上升。",
            metadata={"service": "payment-service", "document_type": "runbook"},
        ),
        KnowledgeChunk(
            chunk_id="payment-cache",
            source_id="payment-cache-guide",
            index=0,
            content="缓存命中率下降会增加支付服务延迟。",
            metadata={"service": "payment-service", "document_type": "guide"},
        ),
        KnowledgeChunk(
            chunk_id="order-db",
            source_id="order-runbook",
            index=0,
            content="订单服务数据库慢查询会导致请求超时。",
            metadata={"service": "order-service", "document_type": "runbook"},
        ),
    ]


def test_bm25_returns_most_relevant_chunk_first() -> None:
    """查询中的关键事实应将对应 Runbook 排在首位。"""
    hits = BM25Retriever(make_chunks()).search("支付 数据库连接池 超时", top_k=2)

    assert [hit.chunk.chunk_id for hit in hits] == ["payment-db", "order-db"]
    assert [hit.rank for hit in hits] == [1, 2]
    assert hits[0].score > hits[1].score


def test_metadata_filter_applies_before_ranking() -> None:
    """元数据过滤应排除不属于目标服务的相似关键词结果。"""
    hits = BM25Retriever(make_chunks()).search(
        "数据库 超时",
        metadata_filter={"service": "payment-service"},
    )

    assert [hit.chunk.chunk_id for hit in hits] == ["payment-db"]


def test_tie_breaker_is_stable_by_chunk_id() -> None:
    """相同 BM25 分数必须按 chunk_id 排序，避免评测顺序波动。"""
    chunks = [
        KnowledgeChunk(
            chunk_id="b",
            source_id="source-b",
            index=0,
            content="连接池超时",
        ),
        KnowledgeChunk(
            chunk_id="a",
            source_id="source-a",
            index=0,
            content="连接池超时",
        ),
    ]

    hits = BM25Retriever(chunks).search("连接池超时")

    assert [hit.chunk.chunk_id for hit in hits] == ["a", "b"]


def test_invalid_search_parameters_are_rejected() -> None:
    """空查询或非正 Top-K 不应静默产生无意义检索。"""
    retriever = BM25Retriever(make_chunks())

    with pytest.raises(ValueError, match="query"):
        retriever.search("   ")

    with pytest.raises(ValueError, match="top_k"):
        retriever.search("超时", top_k=0)
