"""QdrantVectorStore 的验收测试。"""

import pytest
from app.rag.qdrant_store import QdrantVectorStore
from qdrant_client import QdrantClient

from app.models.contracts import KnowledgeChunk, VectorizedChunk


def make_records() -> list[VectorizedChunk]:
    """构造用于真实 Qdrant 内存模式的向量分块。"""
    return [
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="payment-db",
                source_id="payment-runbook",
                index=0,
                content="支付服务数据库连接池耗尽。",
                metadata={"service": "payment-service", "document_type": "runbook"},
            ),
            vector=[1.0, 0.0],
        ),
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="payment-cache",
                source_id="payment-cache-guide",
                index=0,
                content="支付服务缓存命中率下降。",
                metadata={"service": "payment-service", "document_type": "guide"},
            ),
            vector=[0.6, 0.8],
        ),
        VectorizedChunk(
            chunk=KnowledgeChunk(
                chunk_id="order-db",
                source_id="order-runbook",
                index=0,
                content="订单服务数据库慢查询。",
                metadata={"service": "order-service", "document_type": "runbook"},
            ),
            vector=[0.0, 1.0],
        ),
    ]


def make_store() -> QdrantVectorStore:
    """构造独立的 Qdrant 内存集合。"""
    return QdrantVectorStore(
        client=QdrantClient(":memory:"),
        collection_name="knowledge_chunks",
        vector_size=2,
    )


def test_upsert_creates_collection_and_returns_ranked_hits() -> None:
    """首次写入应建集合，并按余弦相似度返回来源完整的分块。"""
    store = make_store()
    store.upsert(make_records())

    hits = store.search([1.0, 0.0], top_k=2)

    assert [hit.chunk.chunk_id for hit in hits] == ["payment-db", "payment-cache"]
    assert [hit.rank for hit in hits] == [1, 2]
    assert hits[0].chunk.metadata["document_type"] == "runbook"


def test_upsert_is_idempotent_and_metadata_filter_reaches_qdrant() -> None:
    """稳定点 ID 应避免重复写入，且过滤条件应由 Qdrant 执行。"""
    store = make_store()
    payment_record = make_records()[0]
    store.upsert([payment_record])
    store.upsert([payment_record])

    hits = store.search(
        [1.0, 0.0],
        metadata_filter={"service": "payment-service"},
    )

    assert [hit.chunk.chunk_id for hit in hits] == ["payment-db"]


def test_metadata_filter_excludes_other_services() -> None:
    """嵌套 metadata 字段必须限制 Qdrant 的候选点。"""
    store = make_store()
    store.upsert(make_records())

    hits = store.search(
        [1.0, 0.0],
        metadata_filter={"service": "order-service"},
    )

    assert hits == []


def test_store_rejects_invalid_configuration_and_vector_dimensions() -> None:
    """集合名、维度、查询向量和写入向量必须显式校验。"""
    with pytest.raises(ValueError, match="collection_name"):
        QdrantVectorStore(
            client=QdrantClient(":memory:"),
            collection_name=" ",
            vector_size=2,
        )
    with pytest.raises(ValueError, match="vector_size"):
        QdrantVectorStore(
            client=QdrantClient(":memory:"),
            collection_name="knowledge_chunks",
            vector_size=0,
        )

    store = make_store()
    with pytest.raises(ValueError, match="dimension"):
        store.upsert(
            [
                VectorizedChunk(
                    chunk=KnowledgeChunk(
                        chunk_id="wrong-size",
                        source_id="source",
                        index=0,
                        content="错误维度。",
                    ),
                    vector=[1.0],
                )
            ]
        )
    with pytest.raises(ValueError, match="dimension"):
        store.search([1.0])
    with pytest.raises(ValueError, match="top_k"):
        store.search([1.0, 0.0], top_k=0)
