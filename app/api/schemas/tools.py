"""工具目录 API 的 HTTP 响应模型。"""

from pydantic import BaseModel, ConfigDict, Field

from app.models.contracts import ToolRiskLevel


class ToolSummary(BaseModel):
    """供工作台展示和请求预检的工具策略摘要。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    risk_level: ToolRiskLevel
    read_only: bool
    requires_approval: bool
    required_args: list[str]
    # None 表示工具尚未声明参数 schema，不能误解为不接收参数。
    allowed_args: list[str] | None
    max_calls_per_run: int | None
