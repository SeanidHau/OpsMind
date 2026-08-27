"""FastAPI 应用工厂与 ASGI 入口。"""

from fastapi import FastAPI

from app.api.middleware import RequestContextMiddleware
from app.api.routers.scenarios import router as scenarios_router
from app.api.routers.system import router as system_router
from app.api.routers.tools import router as tools_router
from app.api.version import API_VERSION
from app.config import AppEnvironment, Settings, get_settings
from app.observability.logging import configure_logging
from app.scenarios.defaults import create_default_scenario_store
from app.tools.registry import ToolRegistry
from app.tools.scenarios import ScenarioStore, register_scenario_tools


def create_app(
    *, settings: Settings | None = None, scenario_store: ScenarioStore | None = None
) -> FastAPI:
    """创建应用实例，并注入可替换的配置供后续依赖使用。"""
    resolved_settings = settings or get_settings()
    configure_logging(log_level=resolved_settings.log_level)

    app = FastAPI(
        title="OpsMind API",
        version=API_VERSION,
        description="面向受控运维诊断 Agent 的 HTTP 服务。",
        # 仅在开发环境启用调试模式，避免生产环境泄露调用栈。
        debug=resolved_settings.app_env is AppEnvironment.DEVELOPMENT,
        # Redoc 暂不启用，后续接口较多时再评估是否保留。
        redoc_url=None,
    )

    # 保存解析后的配置；路由层不应自行读取环境变量。
    app.state.settings = resolved_settings

    app.state.scenario_store = scenario_store or create_default_scenario_store()

    # 每个应用实例使用独立注册表；工具与场景存储保持同一注入来源。
    tool_registry = ToolRegistry()
    register_scenario_tools(tool_registry, app.state.scenario_store)
    app.state.tool_registry = tool_registry

    app.add_middleware(RequestContextMiddleware)

    # 所有公开 API 使用 /api/v1 前缀，便于后续版本演进。
    app.include_router(system_router)
    app.include_router(scenarios_router)
    app.include_router(tools_router)
    return app


# 供 `uvicorn app.api.main:app` 直接启动。
app = create_app()
