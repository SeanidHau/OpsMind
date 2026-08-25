"""FastAPI 服务骨架的验收测试。"""

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.version import API_VERSION


def test_create_app_exposes_versioned_health_endpoint() -> None:
    """健康检查必须通过真实 HTTP 路由返回稳定响应。"""
    app = create_app()

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
    app = create_app()

    assert app.title == "OpsMind API"
    assert app.version == API_VERSION
    assert app.redoc_url is None
