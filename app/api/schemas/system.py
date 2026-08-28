"""系统级接口的响应模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """服务存活检查的稳定响应契约。"""

    # 禁止客户端误以为响应中存在未声明字段。
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    service: Literal["opsmind"]
    version: str


class ReadinessResponse(BaseModel):
    """服务依赖就绪状态的安全响应契约。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    postgres: Literal["ok", "not_configured", "unavailable"]
    milvus: Literal["ok", "unavailable"]
