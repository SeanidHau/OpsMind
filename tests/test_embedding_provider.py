"""OpenAI 兼容 Embedding 客户端装配的验收测试。"""

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.rag.embeddings import EmbeddingConfigurationError, create_embedding_client


class FakeEmbeddings:
    """记录构造参数，不访问外部 Embedding 服务。"""

    instances: list["FakeEmbeddings"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        del texts
        return []

    def embed_query(self, text: str) -> list[float]:
        del text
        return []


def test_embedding_client_is_disabled_without_embedding_model() -> None:
    """未配置模型时，应用不应隐式创建外部客户端。"""
    assert create_embedding_client(Settings(_env_file=None)) is None


def test_embedding_client_uses_shared_openai_compatible_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedding 可复用通用密钥和 Base URL。"""
    FakeEmbeddings.instances = []
    monkeypatch.setattr("app.rag.embeddings.OpenAIEmbeddings", FakeEmbeddings)

    client = create_embedding_client(
        Settings(
            _env_file=None,
            embedding_model="text-embedding-3-small",
            llm_api_key="shared-key",
            llm_base_url="https://gateway.example.test/v1",
        )
    )

    assert isinstance(client, FakeEmbeddings)
    assert FakeEmbeddings.instances[0].kwargs == {
        "model": "text-embedding-3-small",
        "api_key": SecretStr("shared-key"),
        "base_url": "https://gateway.example.test/v1",
    }


def test_embedding_client_requires_a_key_when_model_is_configured() -> None:
    """配置模型但缺少密钥时必须在启动前失败。"""
    with pytest.raises(EmbeddingConfigurationError, match="embedding_api_key"):
        create_embedding_client(Settings(_env_file=None, embedding_model="text-embedding-3-small"))
