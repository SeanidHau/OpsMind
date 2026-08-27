"""可扩展模型提供器工厂和应用自动装配的验收测试。"""

from typing import Any

import pytest
from pydantic import SecretStr

from app.agents.action_provider import LangChainActionProvider
from app.api.main import create_app
from app.config import Settings
from app.diagnosis.providers import (
    ModelProviderConfigurationError,
    create_action_provider,
)
from app.diagnosis.runner import HarnessDiagnosisRunner


class FakeRunnable:
    """避免测试访问模型供应商的最小结构化输出对象。"""

    async def ainvoke(self, input: object) -> dict[str, object]:
        """本阶段不执行模型调用。"""
        del input
        return {}


class FakeChatModel:
    """记录提供器初始化参数的通用 LangChain 聊天模型替身。"""

    instances: list["FakeChatModel"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def with_structured_output(
        self,
        schema: type[object],
        *,
        include_raw: bool = False,
    ) -> FakeRunnable:
        """返回满足动作提供器初始化需求的替身。"""
        del schema, include_raw
        return FakeRunnable()


def provider_settings(provider: str, **overrides: Any) -> Settings:
    """返回使用通用密钥的最小提供器配置。"""
    values = {
        "llm_provider": provider,
        "llm_model": "test-model",
        "llm_api_key": "shared-test-key",
        # 显式屏蔽开发机环境变量，保证优先级测试不读取本地配置。
        "openai_api_key": None,
        "openai_base_url": None,
        "anthropic_api_key": None,
        "anthropic_base_url": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_provider_factory_returns_none_without_provider_configuration() -> None:
    """未配置模型时，应用应保留运行 API 的不可用状态。"""
    assert create_action_provider(Settings()) is None


@pytest.mark.parametrize(
    ("provider", "module_path", "kwargs"),
    [
        (
            "openai",
            "app.diagnosis.providers.ChatOpenAI",
            {
                "model": "test-model",
                "api_key": SecretStr("shared-test-key"),
                "temperature": 0,
                "base_url": "https://gateway.example.test/v1",
            },
        ),
        (
            "anthropic",
            "app.diagnosis.providers.ChatAnthropic",
            {
                "model": "test-model",
                "api_key": SecretStr("shared-test-key"),
                "temperature": 0,
                "base_url": "https://gateway.example.test/v1",
            },
        ),
    ],
)
def test_provider_factory_builds_supported_models_with_shared_base_url(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    module_path: str,
    kwargs: dict[str, object],
) -> None:
    """OpenAI 和 Anthropic 都可使用通用密钥与基础地址。"""
    FakeChatModel.instances = []
    monkeypatch.setattr(module_path, FakeChatModel)

    action_provider = create_action_provider(
        provider_settings(provider, llm_base_url="https://gateway.example.test/v1")
    )

    assert isinstance(action_provider, LangChainActionProvider)
    assert FakeChatModel.instances[0].kwargs == kwargs


@pytest.mark.parametrize(
    ("provider", "overrides", "expected_key", "expected_base_url"),
    [
        (
            "openai",
            {
                "openai_api_key": "openai-key",
                "openai_base_url": "https://openai.example.test/v1",
            },
            SecretStr("openai-key"),
            "https://openai.example.test/v1",
        ),
        (
            "anthropic",
            {
                "anthropic_api_key": "anthropic-key",
                "anthropic_base_url": "https://anthropic.example.test/v1",
            },
            SecretStr("anthropic-key"),
            "https://anthropic.example.test/v1",
        ),
    ],
)
def test_provider_specific_configuration_overrides_shared_values(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    overrides: dict[str, str],
    expected_key: SecretStr,
    expected_base_url: str,
) -> None:
    """提供器专用密钥和基础地址的优先级必须高于通用字段。"""
    FakeChatModel.instances = []
    target = "ChatOpenAI" if provider == "openai" else "ChatAnthropic"
    monkeypatch.setattr(f"app.diagnosis.providers.{target}", FakeChatModel)

    create_action_provider(
        provider_settings(
            provider,
            llm_api_key="shared-key",
            llm_base_url="https://shared.example.test/v1",
            **overrides,
        )
    )

    assert FakeChatModel.instances[0].kwargs["api_key"] == expected_key
    assert FakeChatModel.instances[0].kwargs["base_url"] == expected_base_url


def test_provider_factory_accepts_extension_registry() -> None:
    """后续厂商可通过提供器协议扩展，无需修改核心分发逻辑。"""

    class CustomProvider:
        def create_chat_model(self, settings: Settings) -> FakeChatModel:
            del settings
            return FakeChatModel(model="custom")

    FakeChatModel.instances = []
    action_provider = create_action_provider(
        provider_settings("custom"),
        providers={"custom": CustomProvider()},
    )

    assert isinstance(action_provider, LangChainActionProvider)
    assert FakeChatModel.instances[0].kwargs == {"model": "custom"}


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            Settings(_env_file=None, llm_provider="unknown"),
            "unsupported llm provider: unknown",
        ),
        (
            Settings(_env_file=None, llm_provider="openai", llm_api_key="test-key"),
            "llm_model is required for openai provider",
        ),
        (
            Settings(_env_file=None, llm_provider="anthropic", llm_model="test-model"),
            "anthropic_api_key or llm_api_key is required for anthropic provider",
        ),
    ],
)
def test_provider_factory_rejects_invalid_model_configuration(
    settings: Settings,
    message: str,
) -> None:
    """未知提供器与不完整配置必须在启动时失败。"""
    with pytest.raises(ModelProviderConfigurationError, match=message):
        create_action_provider(settings)


def test_application_builds_runtime_from_configured_anthropic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """应用工厂在未显式注入动作提供器时使用 Anthropic 配置完成装配。"""
    FakeChatModel.instances = []
    monkeypatch.setattr("app.diagnosis.providers.ChatAnthropic", FakeChatModel)

    app = create_app(settings=provider_settings("anthropic"))

    assert isinstance(app.state.diagnosis_runner, HarnessDiagnosisRunner)
    assert len(FakeChatModel.instances) == 1
