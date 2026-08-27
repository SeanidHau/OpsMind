"""模型提供器到结构化动作提供器的受控适配。"""

from langchain_openai import ChatOpenAI

from app.agents.action_provider import LangChainActionProvider
from app.config import Settings
from app.harness.loop import ActionProvider


class ModelProviderConfigurationError(ValueError):
    """模型提供器配置缺失或不被当前应用支持。"""


def create_action_provider(settings: Settings) -> ActionProvider | None:
    """根据配置创建结构化动作提供器；未配置时返回 None。"""
    if settings.llm_provider is None:
        return None

    provider_name = settings.llm_provider.casefold()
    if provider_name != "openai":
        raise ModelProviderConfigurationError(f"unsupported llm provider: {settings.llm_provider}")

    if settings.llm_model is None:
        raise ModelProviderConfigurationError("llm_model is required for openai provider")
    if settings.openai_api_key is None:
        raise ModelProviderConfigurationError("openai_api_key is required for openai provider")

    if settings.openai_base_url is not None:
        chat_model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            # 降低同一输入在离线评测和演示中的采样差异。
            temperature=0,
            base_url=str(settings.openai_base_url),
        )
    else:
        chat_model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            # 降低同一输入在离线评测和演示中的采样差异。
            temperature=0,
        )

    return LangChainActionProvider(chat_model)
