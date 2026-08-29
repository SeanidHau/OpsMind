"""全局测试夹具。"""

from collections.abc import Callable

import pytest

from app.config import Settings


@pytest.fixture
def isolated_settings(tmp_path) -> Callable[..., Settings]:
    """构造不受开发机 `.env` 影响的应用配置。"""

    def build(**overrides: object) -> Settings:
        defaults: dict[str, object] = {
            "run_archive_backend": "memory",
            "llm_provider": None,
            "llm_model": None,
            "llm_api_key": None,
            "llm_base_url": None,
            "openai_api_key": None,
            "openai_base_url": None,
            "anthropic_api_key": None,
            "anthropic_base_url": None,
            "embedding_model": None,
            "embedding_api_key": None,
            "embedding_base_url": None,
            "mcp_configuration_path": tmp_path / "mcp-configuration.json",
        }
        return Settings(_env_file=None, **(defaults | overrides))

    return build


@pytest.fixture(autouse=True)
def use_isolated_default_application_settings(
    monkeypatch: pytest.MonkeyPatch,
    isolated_settings: Callable[..., Settings],
) -> None:
    """默认应用工厂不应因本机开发配置而改变测试语义。"""
    settings = isolated_settings()
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)
