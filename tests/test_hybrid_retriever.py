"""HybridRetriever 的验收测试。"""

import pytest
from app.rag.hybrid import HybridRetriever

from app.models.contracts import KnowledgeChunk, VectorizedChunk
from app.rag.bm25 import BM25Retriever
from app.rag.vector import InMemoryVectorRetriever


def make_hybrid_retriever() -> HybridRetriever:
    """构造可同时命中关键词和向量证据的混合检索器。"""
    chunks = [
        KnowledgeChunk(
            chunk_id="payment-db",
            source_id="payment-runbook",
            index=0,
            content="支付服务数据库连接池耗尽会导致接口超时。",
            metadata={"service": "payment-service"},
        ),
        KnowledgeChunk(
            chunk_id="payment-cache",
            source_id="payment-cache-guide",
            index=0,
            content="支付服务缓存命中率下降会增加延迟。",
            metadata={"service": "payment-service"},
        ),
        KnowledgeChunk(
            chunk_id="order-db",
            source_id="order-runbook",
            index=0,
            content="订单服务数据库连接池耗尽会导致接口超时。",
            metadata={"service": "order-service"},
        ),
    ]
    vector_records = [
        VectorizedChunk(chunk=chunks[0], vector=[1.0, 0.0]),
        VectorizedChunk(chunk=chunks[1], vector=[0.7, 0.7]),
        VectorizedChunk(chunk=chunks[2], vector=[0.8, 0.2]),
    ]

    return HybridRetriever(
        keyword_retriever=BM25Retriever(chunks),
        vector_retriever=InMemoryVectorRetriever(vector_records),
    )


def test_hybrid_retriever_fuses_keyword_and_vector_hits() -> None:
    """同时被 BM25 与向量检索命中的 Runbook 应位于融合结果首位。"""
    hits = make_hybrid_retriever().search(
        query="支付 数据库连接池 超时",
        query_vector=[1.0, 0.0],
        top_k=2,
    )

    assert hits[0].chunk.chunk_id == "payment-db"
    assert hits[0].retriever_names == ["bm25", "vector"]
    assert [hit.rank for hit in hits] == [1, 2]


def test_hybrid_retriever_passes_filter_to_both_retrievers() -> None:
    """服务过滤条件应约束关键词和向量两条检索路径。"""
    hits = make_hybrid_retriever().search(
        query="数据库连接池 超时",
        query_vector=[1.0, 0.0],
        metadata_filter={"service": "payment-service"},
    )

    assert {hit.chunk.metadata["service"] for hit in hits} == {"payment-service"}


def test_hybrid_retriever_returns_empty_when_both_paths_have_no_hit() -> None:
    """两条路径均无正相关结果时，应返回空列表。"""
    hits = make_hybrid_retriever().search(
        query="unmatched",
        query_vector=[-1.0, 0.0],
    )

    assert hits == []


def test_hybrid_retriever_rejects_non_positive_top_k() -> None:
    """组合层必须在调用下游检索器前拒绝无效 Top-K。"""
    with pytest.raises(ValueError, match="top_k"):
        make_hybrid_retriever().search(
            query="支付",
            query_vector=[1.0, 0.0],
            top_k=0,
        )
