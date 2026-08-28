"""面向 Harness 的只读知识检索工具。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.models.contracts import ToolDefinition, ToolRiskLevel
from app.rag.search import KnowledgeSearcher
from app.tools.registry import ToolRegistry


def register_knowledge_tools(registry: ToolRegistry, searcher: KnowledgeSearcher) -> None:
    """注册单次返回最多三个来源分块的低风险知识检索工具。"""

    async def query_knowledge(args: dict[str, Any]) -> dict[str, Any]:
        """在线程中执行同步 Embedding 和 Milvus 查询。"""
        query = str(args["query"]).strip()
        service = str(args.get("service", "")).strip()
        hits = await asyncio.to_thread(
            searcher.search,
            query,
            metadata_filter={"service": service} if service else None,
        )
        return {
            "query": query,
            "count": len(hits),
            "hits": [
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "source_id": hit.chunk.source_id,
                    "content": hit.chunk.content,
                    "metadata": hit.chunk.metadata,
                    "score": hit.score,
                }
                for hit in hits
            ],
        }

    registry.register(
        ToolDefinition(
            name="query_knowledge",
            description="检索运维知识库中的 Runbook 和故障处理说明。",
            risk_level=ToolRiskLevel.LOW,
            read_only=True,
            required_args=("query",),
            allowed_args=("query", "service"),
            max_calls_per_run=2,
        ),
        query_knowledge,
    )
