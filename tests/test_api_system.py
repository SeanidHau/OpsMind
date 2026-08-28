"""FastAPI 服务骨架的验收测试。"""

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.version import API_VERSION
from app.config import AppEnvironment, Settings


def make_settings() -> Settings:
    """构造不依赖本机环境变量的系统路由测试配置。"""
    return Settings(
        _env_file=None,
        app_env=AppEnvironment.TEST,
        database_url="postgresql+asyncpg://opsmind:password@localhost:5432/opsmind",
        run_archive_backend="memory",
        milvus_url="http://localhost:19530",
    )


def test_create_app_exposes_versioned_health_endpoint() -> None:
    """健康检查必须通过真实 HTTP 路由返回稳定响应。"""
    app = create_app(settings=make_settings())

    # TestClient 在进程内运行 ASGI 应用，不需要启动 Uvicorn。
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "opsmind",
        "version": API_VERSION,
    }


def test_create_app_exposes_openapi_metadata() -> None:
    """应用工厂必须提供稳定的服务名称和版本。"""
    app = create_app(settings=make_settings())

    assert app.title == "OpsMind API"
    assert app.version == API_VERSION
    assert app.redoc_url is None


def test_readiness_reports_memory_archive_and_available_milvus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """就绪接口在依赖可访问时返回固定安全状态。"""

    class AvailableMilvusClient:
        def __init__(self, *, uri: str) -> None:
            assert uri == "http://localhost:19530/"

        def list_collections(self) -> list[str]:
            return []

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.api.routers.system.MilvusClient", AvailableMilvusClient)
    with TestClient(create_app(settings=make_settings())) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "postgres": "not_configured",
        "milvus": "ok",
    }


def test_readiness_hides_milvus_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """依赖失败返回 503 和固定状态，不泄露网络异常文本。"""

    class UnavailableMilvusClient:
        def __init__(self, *, uri: str) -> None:
            del uri
            raise RuntimeError("internal connection string")

    monkeypatch.setattr("app.api.routers.system.MilvusClient", UnavailableMilvusClient)
    with TestClient(create_app(settings=make_settings())) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "postgres": "not_configured",
        "milvus": "unavailable",
    }
