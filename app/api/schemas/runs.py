"""诊断运行 API 的请求与响应模型。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.contracts import ActionType, EventType, HarnessStatus, ReplayMode


class CreateDiagnosisRunRequest(BaseModel):
    """创建一次受控诊断运行的输入。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=200)
    thread_id: str = Field(min_length=1, max_length=200)
    user_query: str = Field(min_length=1, max_length=4_000)


class PendingApprovalResponse(BaseModel):
    """等待审批时可公开展示的最小摘要。"""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2_000)


class DiagnosisRunResponse(BaseModel):
    """诊断运行结束或暂停后返回的最小安全摘要。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: HarnessStatus | None
    step_count: int = Field(ge=0)
    final_answer: str | None
    pending_question: str | None
    pending_approval: PendingApprovalResponse | None
    errors: list[str]


class DiagnosisRunHistoryItem(BaseModel):
    """历史列表所需的最小安全运行摘要。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: HarnessStatus | None
    step_count: int = Field(ge=0)
    query: str = Field(min_length=1, max_length=200)
    captured_at: datetime


class DiagnosisRunHistoryResponse(BaseModel):
    """按最新优先返回的有限历史记录。"""

    model_config = ConfigDict(extra="forbid")

    runs: list[DiagnosisRunHistoryItem]


class DiagnosisTrajectoryEventResponse(BaseModel):
    """诊断轨迹中可安全公开的一条审计事件。"""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    step_id: int = Field(ge=0)
    event_type: EventType
    timestamp: datetime
    node: str | None
    action_type: ActionType | None
    tool_name: str | None
    latency_ms: int | None = Field(default=None, ge=0)
    token_usage: dict[str, int | float] | None
    decision: str | None
    error: str | None


class DiagnosisRunTrajectoryResponse(BaseModel):
    """已归档诊断运行的安全轨迹视图。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    mode: ReplayMode
    status: HarnessStatus | None
    event_count: int = Field(ge=0)
    events: list[DiagnosisTrajectoryEventResponse]


class ResumeDiagnosisRunRequest(BaseModel):
    """向等待输入的诊断运行提交一条用户回答。"""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=4_000)

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        """去除无意义留白，避免把空回答写入运行历史。"""
        normalized_answer = value.strip()
        if not normalized_answer:
            raise ValueError("answer must not be blank")
        return normalized_answer
