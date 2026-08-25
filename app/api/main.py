"""FastAPI 应用工厂与 ASGI 入口。"""

from fastapi import FastAPI

from app.api.routers.system import router as system_router
from app.api.version import API_VERSION


def create_app() -> FastAPI:
    """创建独立的应用实例，便于测试和未来注入基础设施依赖。"""
    app = FastAPI(
        title="OpsMind API",
        version=API_VERSION,
        description="面向受控运维诊断 Agent 的 HTTP 服务。",
        # Redoc 暂不启用，后续接口较多时再评估是否保留。
        redoc_url=None,
    )

    # 所有公开 API 使用 /api/v1 前缀，便于后续版本演进。
    app.include_router(system_router)
    return app


# 供 `uvicorn app.api.main:app` 直接启动。
app = create_app()
