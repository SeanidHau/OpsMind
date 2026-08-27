"""应用配置的加载、校验与缓存。"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    """OpsMind 支持的运行环境。"""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """从环境变量和本地 `.env` 加载应用配置。"""

    # `.env` 仅用于本地开发；部署环境优先使用真实环境变量。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # 基础设施地址在此校验，但本阶段不建立连接。
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://opsmind:opsmind_dev_only@127.0.0.1:5432/opsmind"
    )
    qdrant_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:6333")

    # 模型供应商配置保持可选，避免健康检查依赖真实密钥。
    llm_provider: str | None = None
    llm_model: str | None = None
    # 通用字段供后续提供器复用；专用字段优先级更高。
    llm_api_key: SecretStr | None = None
    llm_base_url: AnyHttpUrl | None = None

    openai_api_key: SecretStr | None = None
    openai_base_url: AnyHttpUrl | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: AnyHttpUrl | None = None

    # LangSmith 默认关闭；后续评测阶段再按配置启用。
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "opsmind-dev"

    @field_validator(
        "llm_provider",
        "llm_model",
        "llm_api_key",
        "llm_base_url",
        "openai_api_key",
        "openai_base_url",
        "anthropic_api_key",
        "anthropic_base_url",
        "langsmith_api_key",
        mode="before",
    )
    @classmethod
    def blank_optional_values_are_none(cls, value: object) -> object:
        """将 `.env` 中的空字符串转换为缺省值。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """返回进程内共享的配置实例，避免每次请求重复读取环境变量。"""
    return Settings()
