"""本机 MCP 连接配置的公开 API 契约。"""

from pydantic import BaseModel, ConfigDict, Field


class McpServiceResponse(BaseModel):
    """显示地址与令牌是否存在，但绝不返回令牌本身。"""

    model_config = ConfigDict(extra="forbid")

    url: str | None
    token_configured: bool


class McpConfigurationResponse(BaseModel):
    """桌面工作台读取的 MCP 配置摘要。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    command: str
    arguments: str
    prometheus: McpServiceResponse
    loki: McpServiceResponse
    jaeger: McpServiceResponse
    kubernetes: McpServiceResponse
    cmdb: McpServiceResponse


class McpConfigurationUpdate(BaseModel):
    """桌面端提交的本机 MCP 配置；空令牌保留已保存的令牌。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    command: str = Field(min_length=1, max_length=200)
    arguments: str = Field(max_length=500)
    prometheus_url: str = Field(default="", max_length=2_000)
    prometheus_bearer_token: str = Field(default="", max_length=4_000)
    loki_url: str = Field(default="", max_length=2_000)
    loki_bearer_token: str = Field(default="", max_length=4_000)
    jaeger_url: str = Field(default="", max_length=2_000)
    jaeger_bearer_token: str = Field(default="", max_length=4_000)
    kubernetes_url: str = Field(default="", max_length=2_000)
    kubernetes_bearer_token: str = Field(default="", max_length=4_000)
    cmdb_url: str = Field(default="", max_length=2_000)
    cmdb_bearer_token: str = Field(default="", max_length=4_000)
