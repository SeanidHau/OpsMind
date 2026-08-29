"""本机 MCP 连接配置 API 的验收测试。"""

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import AppEnvironment, Settings


def make_settings(configuration_path: str) -> Settings:
    """构造不读取用户 `.env` 的隔离配置。"""
    return Settings(
        _env_file=None,
        app_env=AppEnvironment.TEST,
        database_url="postgresql+asyncpg://opsmind:password@localhost:5432/opsmind",
        milvus_url="http://localhost:19530",
        run_archive_backend="memory",
        mcp_configuration_path=configuration_path,
        llm_provider=None,
        llm_model=None,
        llm_api_key=None,
        llm_base_url=None,
        embedding_model=None,
        embedding_api_key=None,
        embedding_base_url=None,
        embedding_vector_size=1_536,
    )


def test_mcp_configuration_can_be_saved_without_returning_tokens(tmp_path) -> None:
    """令牌仅保存到本机文件，GET 响应只说明令牌是否已配置。"""
    configuration_path = tmp_path / "mcp.json"
    payload = {
        "enabled": True,
        "command": "uv",
        "arguments": "run python -m app.mcp.observability_server",
        "prometheus_url": "http://prometheus.local:9090",
        "prometheus_bearer_token": "read-only-token",
        "loki_url": "",
        "loki_bearer_token": "",
        "jaeger_url": "",
        "jaeger_bearer_token": "",
        "kubernetes_url": "",
        "kubernetes_bearer_token": "",
        "cmdb_url": "",
        "cmdb_bearer_token": "",
        "llm_provider": "openai",
        "llm_model": "gpt-4.1-mini",
        "llm_api_key": "model-secret",
        "llm_base_url": "",
    }
    with TestClient(create_app(settings=make_settings(str(configuration_path)))) as client:
        response = client.put("/api/v1/mcp", json=payload)
        catalog = client.get("/api/v1/mcp")
        tools = client.get("/api/v1/tools")

    assert response.status_code == 200
    assert catalog.status_code == 200
    assert catalog.json()["prometheus"] == {
        "url": "http://prometheus.local:9090/",
        "token_configured": True,
    }
    assert "read-only-token" not in catalog.text
    assert catalog.json()["model"] == {
        "llm_provider": "openai",
        "llm_model": "gpt-4.1-mini",
        "llm_base_url": None,
        "llm_api_key_configured": True,
        "embedding_model": None,
        "embedding_base_url": None,
        "embedding_api_key_configured": False,
        "embedding_vector_size": 1536,
    }
    assert "model-secret" not in catalog.text
    assert "read-only-token" in configuration_path.read_text(encoding="utf-8")
    assert "model-secret" in configuration_path.read_text(encoding="utf-8")
    assert "query_prometheus" in {item["name"] for item in tools.json()}
    assert not {
        "query_loki",
        "query_jaeger",
        "query_kubernetes",
        "query_cmdb",
    } & {item["name"] for item in tools.json()}
