"""FastAPI 应用工厂与 ASGI 入口。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pymilvus import MilvusClient  # type: ignore[import-untyped]

from app.api.middleware import RequestContextMiddleware
from app.api.routers.knowledge import router as knowledge_router
from app.api.routers.mcp import router as mcp_router
from app.api.routers.runs import router as runs_router
from app.api.routers.scenarios import router as scenarios_router
from app.api.routers.system import router as system_router
from app.api.routers.tools import router as tools_router
from app.api.version import API_VERSION
from app.config import AppEnvironment, Settings, get_settings
from app.diagnosis.providers import create_action_provider
from app.diagnosis.runner import DiagnosisRunner
from app.diagnosis.runtime import create_harness_diagnosis_runner
from app.harness.loop import ActionProvider
from app.harness.snapshot import InMemoryRunArchive, PostgresRunArchive, RunArchive
from app.mcp.configuration import McpConfiguration, McpConfigurationStore
from app.observability.langsmith import create_langsmith_tracer
from app.observability.logging import configure_logging
from app.rag.bm25 import BM25Retriever
from app.rag.documents import load_markdown_chunks
from app.rag.embeddings import create_embedding_client
from app.rag.milvus_store import MilvusVectorStore
from app.rag.search import KnowledgeSearcher
from app.scenarios.defaults import create_default_scenario_store
from app.tools.knowledge import register_knowledge_tools
from app.tools.mcp_adapter import StdioMcpToolInvoker, register_mcp_observability_tools
from app.tools.prometheus import PrometheusClient, register_prometheus_tools
from app.tools.registry import ToolRegistry
from app.tools.scenarios import ScenarioStore, register_scenario_tools


def create_run_archive(settings: Settings) -> RunArchive:
    """按配置选择内存或 PostgreSQL 运行快照归档。"""
    if settings.run_archive_backend == "postgres":
        return PostgresRunArchive(str(settings.database_url))
    return InMemoryRunArchive()


def create_knowledge_searcher(settings: Settings) -> KnowledgeSearcher | None:
    """配置 Embedding 后创建与 Milvus 集合匹配的检索器。"""
    embedder = create_embedding_client(settings)
    if embedder is None:
        return None
    return KnowledgeSearcher(
        embedder=embedder,
        vector_store=MilvusVectorStore(
            client=MilvusClient(uri=str(settings.milvus_url)),
            collection_name=settings.knowledge_collection_name,
            vector_size=settings.embedding_vector_size,
        ),
        keyword_retriever=BM25Retriever(load_markdown_chunks(settings.knowledge_source_directory)),
    )


def create_tool_registry(
    *,
    settings: Settings,
    scenario_store: ScenarioStore,
    knowledge_searcher: KnowledgeSearcher | None,
    mcp_configuration: McpConfiguration,
) -> ToolRegistry:
    """按当前连接配置构建 Harness 可调用的受控工具目录。"""
    tool_registry = ToolRegistry()
    register_scenario_tools(tool_registry, scenario_store)
    if mcp_configuration.enabled:
        configured_mcp_tools = {
            "query_prometheus": mcp_configuration.prometheus,
            "query_loki": mcp_configuration.loki,
            "query_jaeger": mcp_configuration.jaeger,
            "query_kubernetes": mcp_configuration.kubernetes,
            "query_cmdb": mcp_configuration.cmdb,
        }
        register_mcp_observability_tools(
            tool_registry,
            StdioMcpToolInvoker(
                command=mcp_configuration.command,
                arguments=mcp_configuration.arguments,
                environment=mcp_configuration.server_environment(),
            ),
            available_tools=tuple(
                name for name, service in configured_mcp_tools.items() if service.url is not None
            ),
        )
    elif settings.prometheus_url is not None:
        register_prometheus_tools(
            tool_registry,
            PrometheusClient(
                base_url=str(settings.prometheus_url),
                bearer_token=(
                    settings.prometheus_bearer_token.get_secret_value()
                    if settings.prometheus_bearer_token is not None
                    else None
                ),
            ),
        )
    if knowledge_searcher is not None:
        register_knowledge_tools(tool_registry, knowledge_searcher)
    return tool_registry


def create_app(
    *,
    settings: Settings | None = None,
    scenario_store: ScenarioStore | None = None,
    diagnosis_runner: DiagnosisRunner | None = None,
    action_provider: ActionProvider | None = None,
    run_archive: RunArchive | None = None,
    knowledge_searcher: KnowledgeSearcher | None = None,
) -> FastAPI:
    """创建应用实例，并注入可替换的配置供后续依赖使用。"""
    if diagnosis_runner is not None and action_provider is not None:
        raise ValueError("diagnosis_runner and action_provider cannot be provided together")

    base_settings = settings or get_settings()
    mcp_configuration_store = McpConfigurationStore(base_settings.mcp_configuration_path)
    mcp_configuration = mcp_configuration_store.load(McpConfiguration.from_settings(base_settings))
    resolved_settings = mcp_configuration.apply_to_settings(base_settings)
    configure_logging(log_level=resolved_settings.log_level)

    resolved_action_provider = action_provider
    resolved_run_archive = run_archive or create_run_archive(resolved_settings)
    resolved_knowledge_searcher = knowledge_searcher or create_knowledge_searcher(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if isinstance(resolved_run_archive, PostgresRunArchive):
            await resolved_run_archive.initialize()
        try:
            yield
        finally:
            if isinstance(resolved_run_archive, PostgresRunArchive):
                await resolved_run_archive.close()
            active_knowledge_searcher = app.state.knowledge_searcher
            if active_knowledge_searcher is not None:
                await asyncio.to_thread(active_knowledge_searcher.close)

    app = FastAPI(
        title="OpsMind API",
        version=API_VERSION,
        description="面向受控运维诊断 Agent 的 HTTP 服务。",
        # 仅在开发环境启用调试模式，避免生产环境泄露调用栈。
        debug=resolved_settings.app_env is AppEnvironment.DEVELOPMENT,
        # Redoc 暂不启用，后续接口较多时再评估是否保留。
        redoc_url=None,
        lifespan=lifespan,
    )

    # 保存解析后的配置；路由层不应自行读取环境变量。
    app.state.settings = resolved_settings
    app.state.mcp_configuration = mcp_configuration
    app.state.mcp_configuration_store = mcp_configuration_store
    app.state.run_archive = resolved_run_archive
    app.state.knowledge_searcher = resolved_knowledge_searcher

    app.state.scenario_store = scenario_store or create_default_scenario_store()

    # 每个应用实例使用独立注册表；工具与场景存储保持同一注入来源。
    tool_registry = create_tool_registry(
        settings=resolved_settings,
        scenario_store=app.state.scenario_store,
        knowledge_searcher=resolved_knowledge_searcher,
        mcp_configuration=mcp_configuration,
    )
    app.state.tool_registry = tool_registry
    if resolved_action_provider is None:
        resolved_action_provider = create_action_provider(
            resolved_settings,
            tool_definitions=tool_registry.definitions(),
        )
    app.state.diagnosis_runner = diagnosis_runner or (
        create_harness_diagnosis_runner(
            action_provider=resolved_action_provider,
            tool_registry=tool_registry,
            run_archive=resolved_run_archive,
            tracer=create_langsmith_tracer(resolved_settings),
        )
        if resolved_action_provider is not None
        else None
    )

    if diagnosis_runner is None and action_provider is None:

        def reconfigure_mcp(configuration: McpConfiguration) -> None:
            """应用新的本机设置，后续诊断立即使用新的模型与工具目录。"""
            updated_settings = configuration.apply_to_settings(base_settings)
            updated_knowledge_searcher = create_knowledge_searcher(updated_settings)
            updated_registry = create_tool_registry(
                settings=updated_settings,
                scenario_store=app.state.scenario_store,
                knowledge_searcher=updated_knowledge_searcher,
                mcp_configuration=configuration,
            )
            updated_action_provider = create_action_provider(
                updated_settings,
                tool_definitions=updated_registry.definitions(),
            )
            previous_knowledge_searcher = app.state.knowledge_searcher
            app.state.settings = updated_settings
            app.state.mcp_configuration = configuration
            app.state.knowledge_searcher = updated_knowledge_searcher
            app.state.tool_registry = updated_registry
            app.state.diagnosis_runner = (
                create_harness_diagnosis_runner(
                    action_provider=updated_action_provider,
                    tool_registry=updated_registry,
                    run_archive=resolved_run_archive,
                    tracer=create_langsmith_tracer(updated_settings),
                )
                if updated_action_provider is not None
                else None
            )
            if previous_knowledge_searcher is not None:
                previous_knowledge_searcher.close()

        app.state.reconfigure_mcp = reconfigure_mcp

    app.add_middleware(RequestContextMiddleware)

    # 所有公开 API 使用 /api/v1 前缀，便于后续版本演进。
    app.include_router(system_router)
    app.include_router(knowledge_router)
    app.include_router(mcp_router)
    app.include_router(scenarios_router)
    app.include_router(tools_router)
    app.include_router(runs_router)
    return app


# 供 `uvicorn app.api.main:app` 直接启动。
app = create_app()
