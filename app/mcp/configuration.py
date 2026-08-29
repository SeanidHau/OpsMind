"""本机 MCP 观测连接的持久化配置。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr

from app.config import Settings


class McpServiceConfiguration(BaseModel):
    """单个外部系统的地址与仅本机保存的只读令牌。"""

    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl | None = None
    bearer_token: SecretStr | None = None


class ModelConfiguration(BaseModel):
    """LLM 与 Embedding 的本机配置，密钥仅保存于本机。"""

    model_config = ConfigDict(extra="forbid")

    llm_provider: str | None = Field(default=None, max_length=50)
    llm_model: str | None = Field(default=None, max_length=200)
    llm_api_key: SecretStr | None = None
    llm_base_url: AnyHttpUrl | None = None
    embedding_model: str | None = Field(default=None, max_length=200)
    embedding_api_key: SecretStr | None = None
    embedding_base_url: AnyHttpUrl | None = None
    embedding_vector_size: int | None = Field(default=None, gt=0)


class McpConfiguration(BaseModel):
    """供桌面工作台保存的本机模型与 MCP 连接配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    command: str = Field(default="uv", min_length=1, max_length=200)
    arguments: str = Field(default="run python -m app.mcp.observability_server", max_length=500)
    prometheus: McpServiceConfiguration = Field(default_factory=McpServiceConfiguration)
    loki: McpServiceConfiguration = Field(default_factory=McpServiceConfiguration)
    jaeger: McpServiceConfiguration = Field(default_factory=McpServiceConfiguration)
    kubernetes: McpServiceConfiguration = Field(default_factory=McpServiceConfiguration)
    cmdb: McpServiceConfiguration = Field(default_factory=McpServiceConfiguration)
    model: ModelConfiguration = Field(default_factory=ModelConfiguration)

    @classmethod
    def from_settings(cls, settings: Settings) -> McpConfiguration:
        """将已有环境变量配置投影为可由桌面端查看的本机配置。"""
        return cls(
            enabled=settings.observability_mcp_command is not None,
            command=settings.observability_mcp_command or "uv",
            arguments=settings.observability_mcp_args
            or "run python -m app.mcp.observability_server",
            prometheus=McpServiceConfiguration(
                url=settings.prometheus_url, bearer_token=settings.prometheus_bearer_token
            ),
            loki=McpServiceConfiguration(
                url=settings.loki_url, bearer_token=settings.loki_bearer_token
            ),
            jaeger=McpServiceConfiguration(
                url=settings.jaeger_url, bearer_token=settings.jaeger_bearer_token
            ),
            kubernetes=McpServiceConfiguration(
                url=settings.kubernetes_url, bearer_token=settings.kubernetes_bearer_token
            ),
            cmdb=McpServiceConfiguration(
                url=settings.cmdb_url, bearer_token=settings.cmdb_bearer_token
            ),
            model=ModelConfiguration(
                llm_provider=settings.llm_provider,
                llm_model=settings.llm_model,
                llm_api_key=settings.llm_api_key,
                llm_base_url=settings.llm_base_url,
                embedding_model=settings.embedding_model,
                embedding_api_key=settings.embedding_api_key,
                embedding_base_url=settings.embedding_base_url,
                embedding_vector_size=settings.embedding_vector_size,
            ),
        )

    def apply_to_settings(self, settings: Settings) -> Settings:
        """构造供当前进程使用的配置；禁用时保留原有 Prometheus 直连回退。"""
        candidates: dict[str, object] = {
            "llm_provider": self.model.llm_provider or settings.llm_provider,
            "llm_model": self.model.llm_model or settings.llm_model,
            "llm_api_key": self.model.llm_api_key or settings.llm_api_key,
            "llm_base_url": self.model.llm_base_url or settings.llm_base_url,
            "embedding_model": self.model.embedding_model or settings.embedding_model,
            "embedding_api_key": self.model.embedding_api_key or settings.embedding_api_key,
            "embedding_base_url": self.model.embedding_base_url or settings.embedding_base_url,
            "embedding_vector_size": self.model.embedding_vector_size
            or settings.embedding_vector_size,
        }
        if self.enabled:
            candidates.update(
                {
                    "observability_mcp_command": self.command,
                    "observability_mcp_args": self.arguments,
                    "prometheus_url": self.prometheus.url,
                    "prometheus_bearer_token": self.prometheus.bearer_token,
                    "loki_url": self.loki.url,
                    "loki_bearer_token": self.loki.bearer_token,
                    "jaeger_url": self.jaeger.url,
                    "jaeger_bearer_token": self.jaeger.bearer_token,
                    "kubernetes_url": self.kubernetes.url,
                    "kubernetes_bearer_token": self.kubernetes.bearer_token,
                    "cmdb_url": self.cmdb.url,
                    "cmdb_bearer_token": self.cmdb.bearer_token,
                }
            )
        elif settings.observability_mcp_command is not None:
            candidates["observability_mcp_command"] = None
        updates = {
            name: value for name, value in candidates.items() if value != getattr(settings, name)
        }
        if not updates:
            return settings
        return settings.model_copy(update=updates)

    def server_environment(self) -> dict[str, str]:
        """只将 MCP Server 必需的配置传给其子进程，不写入诊断事件。"""
        environment: dict[str, str] = {}
        for prefix, service in (
            ("PROMETHEUS", self.prometheus),
            ("LOKI", self.loki),
            ("JAEGER", self.jaeger),
            ("KUBERNETES", self.kubernetes),
            ("CMDB", self.cmdb),
        ):
            environment[f"{prefix}_URL"] = str(service.url) if service.url is not None else ""
            environment[f"{prefix}_BEARER_TOKEN"] = (
                service.bearer_token.get_secret_value() if service.bearer_token is not None else ""
            )
        return environment


class McpConfigurationStore:
    """将本机 MCP 凭据限制在被忽略且权限收紧的运行目录。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, default: McpConfiguration) -> McpConfiguration:
        """读取已保存配置；无文件或文件损坏时安全回退到环境变量。"""
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                return default
            # 兼容早期仅保存 MCP 字段的本机配置；缺失字段由环境变量补齐。
            return McpConfiguration.model_validate(default.model_dump() | saved)
        except (OSError, ValueError):
            return default

    def save(self, configuration: McpConfiguration) -> None:
        """原子保存本机配置，令牌不进入 API 响应、日志或版本控制。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = configuration.model_dump(mode="json")
        for name in ("prometheus", "loki", "jaeger", "kubernetes", "cmdb"):
            service = getattr(configuration, name)
            payload[name]["bearer_token"] = (
                service.bearer_token.get_secret_value()
                if service.bearer_token is not None
                else None
            )
        payload["model"]["llm_api_key"] = (
            configuration.model.llm_api_key.get_secret_value()
            if configuration.model.llm_api_key is not None
            else None
        )
        payload["model"]["embedding_api_key"] = (
            configuration.model.embedding_api_key.get_secret_value()
            if configuration.model.embedding_api_key is not None
            else None
        )
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(self._path)
        os.chmod(self._path, 0o600)
