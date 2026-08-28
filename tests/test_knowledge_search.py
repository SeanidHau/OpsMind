"""KnowledgeSearcher 与 Harness 知识工具的验收测试。"""

from typing import Any

import pytest

from app.models.contracts import ActionType, AgentAction, KnowledgeChunk, RetrievalHit
from app.rag.bm25 import BM25Retriever
from app.rag.search import KnowledgeSearcher
from app.tools.knowledge import register_knowledge_tools
from app.tools.registry import ToolRegistry


class RecordingEmbedder:
    """记录查询文本并返回确定性向量。"""

    def __init__(self) -> None:
        self.query: str | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        del texts
        return []

    def embed_query(self, text: str) -> list[float]:
        self.query = text
        return [1.0, 0.0]


class RecordingVectorStore:
    """记录查询参数并返回单个可追溯知识分块。"""

    def __init__(self) -> None:
        self.arguments: dict[str, Any] | None = None
        self.closed = False

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        self.arguments = {
            "query_vector": query_vector,
            "top_k": top_k,
            "metadata_filter": metadata_filter,
        }
        return [
            RetrievalHit(
                chunk=KnowledgeChunk(
                    chunk_id="payment-db",
                    source_id="payment-runbook",
                    index=0,
                    content="连接池耗尽会导致支付超时。",
                    metadata={"service": "payment-service"},
                ),
                score=0.9,
                rank=1,
            )
        ]

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_knowledge_tool_vectorizes_query_and_returns_traceable_hits() -> None:
    """知识工具必须通过查询向量检索并返回可引用来源。"""
    embedder = RecordingEmbedder()
    store = RecordingVectorStore()
    registry = ToolRegistry()
    register_knowledge_tools(registry, KnowledgeSearcher(embedder=embedder, vector_store=store))

    result = await registry.execute(
        AgentAction(
            action_type=ActionType.CALL_TOOL,
            intent="检索支付服务 Runbook",
            tool_name="query_knowledge",
            tool_args={"query": " 支付超时 ", "service": "payment-service"},
            reason="补充故障处理知识",
        )
    )

    assert embedder.query == "支付超时"
    assert store.arguments == {
        "query_vector": [1.0, 0.0],
        "top_k": 3,
        "metadata_filter": {"service": "payment-service"},
    }
    assert result == {
        "query": "支付超时",
        "count": 1,
        "hits": [
            {
                "chunk_id": "payment-db",
                "source_id": "payment-runbook",
                "content": "连接池耗尽会导致支付超时。",
                "metadata": {"service": "payment-service"},
                "score": 0.9,
                "retriever_names": ["vector"],
            }
        ],
    }


def test_knowledge_searcher_fuses_keyword_and_vector_hits() -> None:
    """同一分块被两条召回路径命中时，结果必须保留两者来源。"""
    store = RecordingVectorStore()
    keyword_retriever = BM25Retriever(
        [
            KnowledgeChunk(
                chunk_id="payment-db",
                source_id="payment-runbook",
                index=0,
                content="连接池耗尽会导致支付超时。",
                metadata={"service": "payment-service"},
            )
        ]
    )

    hits = KnowledgeSearcher(
        embedder=RecordingEmbedder(),
        vector_store=store,
        keyword_retriever=keyword_retriever,
    ).search("支付超时", metadata_filter={"service": "payment-service"})

    assert hits[0].retriever_names == ["bm25", "vector"]


def test_knowledge_searcher_rejects_blank_query_before_embedding() -> None:
    """空查询不得发往外部 Embedding 服务。"""
    embedder = RecordingEmbedder()
    store = RecordingVectorStore()

    with pytest.raises(ValueError, match="query must not be blank"):
        KnowledgeSearcher(embedder=embedder, vector_store=store).search("  ")

    assert embedder.query is None
    assert store.arguments is None
