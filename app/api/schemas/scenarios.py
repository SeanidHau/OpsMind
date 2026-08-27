"""故障场景目录的 HTTP 响应模型。"""

from pydantic import BaseModel, ConfigDict, Field


class ScenarioSummary(BaseModel):
    """供工作台选择诊断场景的脱敏摘要。"""

    # 目录接口只返回摘要，不返回日志正文和指标数值。
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=100)
    service: str = Field(min_length=1, max_length=100)
    log_count: int = Field(ge=0)
    metric_names: list[str]
    dependency_count: int = Field(ge=0)
