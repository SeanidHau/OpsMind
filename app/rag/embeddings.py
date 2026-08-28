"""OpenAI 兼容 Embedding 客户端的配置装配。"""

from __future__ import annotations

from typing import Protocol

from langchain_openai import OpenAIEmbeddings

from app.config import Settings


class EmbeddingClient(Protocol):
    """RAG 入库与查询共用的最小 Embedding 接口。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为批量文档生成向量。"""

    def embed_query(self, text: str) -> list[float]:
        """为单个检索查询生成向量。"""


class EmbeddingConfigurationError(ValueError):
    """Embedding 配置缺失或不完整时抛出。"""


def create_embedding_client(settings: Settings) -> EmbeddingClient | None:
    """创建 OpenAI Embeddings API 兼容客户端；未配置模型时禁用 RAG 工具。"""
    if settings.embedding_model is None:
        return None

    api_key = settings.embedding_api_key or settings.openai_api_key or settings.llm_api_key
    if api_key is None:
        raise EmbeddingConfigurationError(
            "embedding_api_key, openai_api_key or llm_api_key is required "
            "when embedding_model is set"
        )

    kwargs: dict[str, object] = {
        "model": settings.embedding_model,
        "api_key": api_key,
    }
    base_url = settings.embedding_base_url or settings.openai_base_url or settings.llm_base_url
    if base_url is not None:
        kwargs["base_url"] = str(base_url)
    return OpenAIEmbeddings(**kwargs)  # type: ignore[arg-type]
