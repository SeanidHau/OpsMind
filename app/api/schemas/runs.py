"""诊断运行 API 的请求与响应模型。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.contracts import HarnessStatus


class CreateDiagnosisRunRequest(BaseModel):
    """创建一次受控诊断运行的输入。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=200)
    thread_id: str = Field(min_length=1, max_length=200)
    user_query: str = Field(min_length=1, max_length=4_000)


class DiagnosisRunResponse(BaseModel):
    """诊断运行结束或暂停后返回的最小安全摘要。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: HarnessStatus | None
    step_count: int = Field(ge=0)
    final_answer: str | None
    pending_question: str | None
    errors: list[str]
