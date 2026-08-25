"""应用配置与 FastAPI 配置注入的验收测试。"""

import pytest
from pydantic import ValidationError

from app.api.main import create_app
from app.config import AppEnvironment, Settings, get_settings


def make_settings(**overrides: object) -> Settings:
    """构造不读取外部 `.env` 的确定性测试配置。"""
    defaults = {
        "app_env": AppEnvironment.TEST,
        "log_level": "INFO",
        "database_url": "postgresql+asyncpg://opsmind:password@localhost:5432/opsmind",
        "qdrant_url": "http://localhost:6333",
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
