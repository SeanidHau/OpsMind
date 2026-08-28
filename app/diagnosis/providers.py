"""模型提供器到结构化动作提供器的受控适配。"""

from collections.abc import Mapping
from typing import Protocol, cast

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.agents.action_provider import LangChainActionProvider, StructuredActionChatModel
from app.config import Settings
from app.harness.loop import ActionProvider
from app.models.contracts import ToolDefinition


class ModelProviderConfigurationError(ValueError):
    """模型提供器配置缺失或不被当前应用支持。"""


class ChatModelProvider(Protocol):
    """将应用配置转换为支持结构化输出的 LangChain 聊天模型。"""

    def create_chat_model(self, settings: Settings) -> StructuredActionChatModel:
        """创建模型客户端；不得在此处调用模型。"""


def _required_model(settings: Settings, provider_name: str) -> str:
    """读取当前提供器必须显式配置的模型名称。"""
    if settings.llm_model is None:
        raise ModelProviderConfigurationError(f"llm_model is required for {provider_name} provider")
    return settings.llm_model


def _required_api_key(
    *,
    provider_name: str,
    provider_api_key: SecretStr | None,
    shared_api_key: SecretStr | None,
) -> SecretStr:
    """优先使用提供器专用密钥，回退到通用模型密钥。"""
    api_key = provider_api_key or shared_api_key
    if api_key is None:
        raise ModelProviderConfigurationError(
            f"{provider_name}_api_key or llm_api_key is required for {provider_name} provider"
        )
    return api_key


class OpenAIModelProvider:
    """创建兼容 OpenAI Chat Completions 协议的 LangChain 客户端。"""

    def create_chat_model(self, settings: Settings) -> StructuredActionChatModel:
        """使用 OpenAI 专用配置或通用配置初始化 ChatOpenAI。"""
        model_kwargs: dict[str, object] = {
            "model": _required_model(settings, "openai"),
            "api_key": _required_api_key(
                provider_name="openai",
                provider_api_key=settings.openai_api_key,
                shared_api_key=settings.llm_api_key,
            ),
            # 降低同一输入在离线评测和演示中的采样差异。
            "temperature": 0,
        }
        base_url = settings.openai_base_url or settings.llm_base_url
        if base_url is not None:
            model_kwargs["base_url"] = str(base_url)
        return ChatOpenAI(**model_kwargs)  # type: ignore[arg-type]


class AnthropicModelProvider:
    """创建兼容 Anthropic Messages 协议的 LangChain 客户端。"""

    def create_chat_model(self, settings: Settings) -> StructuredActionChatModel:
        """使用 Anthropic 专用配置或通用配置初始化 ChatAnthropic。"""
        model_kwargs: dict[str, object] = {
            "model": _required_model(settings, "anthropic"),
            "api_key": _required_api_key(
                provider_name="anthropic",
                provider_api_key=settings.anthropic_api_key,
                shared_api_key=settings.llm_api_key,
            ),
            # 降低同一输入在离线评测和演示中的采样差异。
            "temperature": 0,
        }
        base_url = settings.anthropic_base_url or settings.llm_base_url
        if base_url is not None:
            model_kwargs["base_url"] = str(base_url)
        return cast(StructuredActionChatModel, ChatAnthropic(**model_kwargs))  # type: ignore[arg-type]


DEFAULT_MODEL_PROVIDERS: dict[str, ChatModelProvider] = {
    "openai": OpenAIModelProvider(),
    "anthropic": AnthropicModelProvider(),
}


def create_action_provider(
    settings: Settings,
    *,
    providers: Mapping[str, ChatModelProvider] | None = None,
    tool_definitions: tuple[ToolDefinition, ...] = (),
) -> ActionProvider | None:
    """根据配置创建结构化动作提供器；未配置时返回 None。"""
    if settings.llm_provider is None:
        return None

    resolved_providers = {
        **DEFAULT_MODEL_PROVIDERS,
        **{name.casefold(): provider for name, provider in (providers or {}).items()},
    }
    provider_name = settings.llm_provider.casefold()
    provider = resolved_providers.get(provider_name)
    if provider is None:
        raise ModelProviderConfigurationError(f"unsupported llm provider: {settings.llm_provider}")

    return LangChainActionProvider(
        provider.create_chat_model(settings),
        tools=tool_definitions,
    )
