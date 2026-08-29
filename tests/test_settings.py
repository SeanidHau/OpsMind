"""应用配置与 FastAPI 配置注入的验收测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.main import create_app, create_run_archive
from app.config import AppEnvironment, Settings, get_settings
from app.harness.snapshot import InMemoryRunArchive, PostgresRunArchive


def make_settings(**overrides: object) -> Settings:
    """构造不读取外部 `.env` 的确定性测试配置。"""
    defaults = {
        "app_env": AppEnvironment.TEST,
        "log_level": "INFO",
        "database_url": "postgresql+asyncpg://opsmind:password@localhost:5432/opsmind",
        "run_archive_backend": "memory",
        "milvus_url": "http://localhost:19530",
        "mcp_configuration_path": Path(__file__).with_name("mcp-configuration.test.json"),
    }
    return Settings(_env_file=None, **(defaults | overrides))


def test_settings_normalizes_blank_optional_values() -> None:
    """本地 `.env` 中的空可选值不得被误认为有效凭据或地址。"""
    settings = make_settings(
        llm_provider="",
        llm_model="   ",
        openai_api_key="",
        openai_base_url="",
        langsmith_api_key="",
    )

    assert settings.llm_provider is None
    assert settings.llm_model is None
    assert settings.openai_api_key is None
    assert settings.openai_base_url is None
    assert settings.langsmith_api_key is None


def test_settings_rejects_unknown_environment() -> None:
    """运行环境只能使用受控枚举值，避免配置拼写错误静默生效。"""
    with pytest.raises(ValidationError, match="app_env"):
        make_settings(app_env="staging")


def test_settings_require_langsmith_key_when_tracing_is_enabled() -> None:
    """显式启用追踪时，不得静默使用空凭据。"""
    with pytest.raises(ValidationError, match="langsmith_api_key"):
        make_settings(langsmith_tracing=True)


def test_get_settings_caches_a_single_process_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """重复读取配置时应复用实例，避免每个请求重新解析环境变量。"""
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.app_env is AppEnvironment.TEST
    get_settings.cache_clear()


def test_app_factory_uses_injected_settings_and_disables_production_debug() -> None:
    """测试与生产启动都能注入配置，且生产环境不得开启调试模式。"""
    settings = make_settings(app_env=AppEnvironment.PRODUCTION)

    app = create_app(settings=settings)

    assert app.state.settings is settings
    assert app.debug is False


def test_run_archive_backend_uses_explicit_configuration() -> None:
    """默认归档不依赖数据库；显式配置时才创建 PostgreSQL 归档。"""
    assert isinstance(create_run_archive(make_settings()), InMemoryRunArchive)
    assert isinstance(
        create_run_archive(make_settings(run_archive_backend="postgres")),
        PostgresRunArchive,
    )


def test_app_factory_registers_only_configured_mcp_observability_tools() -> None:
    """MCP 启用后，模型只会看到已配置地址对应的只读工具。"""
    app = create_app(
        settings=make_settings(
            observability_mcp_command="uv",
            observability_mcp_args="run python -m app.mcp.observability_server",
            prometheus_url="http://prometheus.example.test:9090",
        )
    )

    definitions = {
        definition.name: definition for definition in app.state.tool_registry.definitions()
    }

    assert "query_prometheus" in definitions
    assert definitions["query_prometheus"].read_only is True
    assert not {
        "query_loki",
        "query_jaeger",
        "query_kubernetes",
        "query_cmdb",
    } & set(definitions)
