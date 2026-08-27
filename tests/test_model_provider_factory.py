"""模型提供器工厂和应用自动装配的验收测试。"""

from typing import Any

import pytest
from pydantic import SecretStr

from app.agents.action_provider import LangChainActionProvider
from app.api.main import create_app
from app.config import Settings
from app.diagnosis.providers import ModelProviderConfigurationError, create_action_provider
from app.diagnosis.runner import HarnessDiagnosisRunner


class FakeRunnable:
    """避免测试访问模型供应商的最小结构化输出对象。"""

    async def ainvoke(self, input: object) -> dict[str, object]:
        """本阶段不执行模型调用。"""
        del input
        return {}


class FakeChatOpenAI:
    """记录 ChatOpenAI 初始化参数的测试替身。"""

    instances: list["FakeChatOpenAI"] = []

    def __init__(self, **kwargs: str | float | SecretStr) -> None:
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


def openai_settings(**overrides: Any) -> Settings:
    """返回满足 OpenAI 提供器要求的最小配置。"""
    return Settings(
        llm_provider="openai",
        llm_model="test-model",
        openai_api_key="test-key",
        **overrides,
    )


def test_provider_factory_returns_none_without_provider_configuration() -> None:
    """未配置模型时，应用应保留运行 API 的不可用状态。"""
    assert create_action_provider(Settings()) is None


def test_openai_provider_factory_builds_structured_action_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI 配置应被转换为低温度的 LangChain 结构化动作提供器。"""
    FakeChatOpenAI.instances = []
    monkeypatch.setattr("app.diagnosis.providers.ChatOpenAI", FakeChatOpenAI)

    provider = create_action_provider(
        openai_settings(openai_base_url="https://gateway.example.test/v1")
    )

    assert isinstance(provider, LangChainActionProvider)
    assert FakeChatOpenAI.instances[0].kwargs == {
        "model": "test-model",
        "api_key": SecretStr("test-key"),
        "temperature": 0,
        "base_url": "https://gateway.example.test/v1",
    }


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (Settings(llm_provider="anthropic"), "unsupported llm provider: anthropic"),
        (
            Settings(llm_provider="openai", openai_api_key="test-key"),
            "llm_model is required for openai provider",
        ),
        (
            Settings(llm_provider="openai", llm_model="test-model"),
            "openai_api_key is required for openai provider",
        ),
    ],
)
def test_provider_factory_rejects_invalid_model_configuration(
    settings: Settings,
    message: str,
) -> None:
    """未知提供器与不完整 OpenAI 配置必须在启动时失败。"""
    with pytest.raises(ModelProviderConfigurationError, match=message):
        create_action_provider(settings)


def test_application_builds_runtime_from_configured_openai_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """应用工厂在未显式注入动作提供器时使用模型配置完成装配。"""
    FakeChatOpenAI.instances = []
    monkeypatch.setattr("app.diagnosis.providers.ChatOpenAI", FakeChatOpenAI)

    app = create_app(settings=openai_settings())

    assert isinstance(app.state.diagnosis_runner, HarnessDiagnosisRunner)
    assert len(FakeChatOpenAI.instances) == 1
