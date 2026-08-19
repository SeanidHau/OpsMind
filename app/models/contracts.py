"""Harness 核心数据契约。

本模块不包含业务流程，只定义 Harness、LangGraph、工具层和评测层之间
共享的数据格式与校验规则。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NotRequired, Self, TypedDict
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间，供轨迹事件统一使用。"""
    return datetime.now(UTC)


class ActionType(StrEnum):
    """模型允许提出的动作类型。"""

    ASK_USER = "ask_user"
    CALL_TOOL = "call_tool"
    UPDATE_PLAN = "update_plan"
    FINAL_ANSWER = "final_answer"
    REQUEST_APPROVAL = "request_approval"
    FAIL = "fail"


class EventType(StrEnum):
    """Harness 写入 trajectory 的事件类型。"""

    PLAN_CREATED = "plan_created"
    CONTEXT_BUILT = "context_built"
    MODEL_CALLED = "model_called"
    ACTION_PROPOSED = "action_proposed"
    ACTION_BLOCKED = "action_blocked"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    OBSERVATION_RECORDED = "observation_recorded"
    VERIFICATION_FAILED = "verification_failed"
    CONTEXT_COMPRESSED = "context_compressed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class PlanStatus(StrEnum):
    """计划项的生命周期状态。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class ProgressStatus(StrEnum):
    """Progress Verifier 对当前步骤给出的结果。"""

    PROGRESSED = "progressed"
    STALLED = "stalled"
    REGRESSED = "regressed"
    COMPLETED = "completed"


class ToolRiskLevel(StrEnum):
    """工具的风险等级，用于决定是否需要人工审批。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PolicyOutcome(StrEnum):
    """策略层对候选动作给出的决策。"""

    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"


class HarnessStatus(StrEnum):
    """Harness Loop 的终止或暂停状态。"""

    COMPLETED = "completed"
    BLOCKED = "blocked"
    WAITING_APPROVAL = "waiting_approval"
    FAILED = "failed"


class BudgetConsumption(BaseModel):
    """描述一个候选动作预计消耗的资源。

    该对象只表达“计划消耗”，不会直接修改 BudgetState。
    """

    model_config = ConfigDict(extra="forbid")

    steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    runtime_seconds: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0)


class ToolPolicy(BaseModel):
    """单个工具的注册信息与执行风险策略。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    risk_level: ToolRiskLevel
    read_only: bool = False
    requires_approval: bool = False


class PolicyDecision(BaseModel):
    """策略层对候选动作给出的决策结果"""

    model_config = ConfigDict(extra="forbid")

    outcome: PolicyOutcome
    reason: str = Field(min_length=1, max_length=2_000)
    consumption: BudgetConsumption
    violations: tuple[str, ...] = ()


class PlanItem(BaseModel):
    """一个可独立跟踪、可完成或可阻塞的任务计划项。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=1_000)
    status: PlanStatus = PlanStatus.PENDING
    depends_on: list[UUID] = Field(default_factory=list)
    notes: str | None = None


class BudgetState(BaseModel):
    """记录一次运行允许消耗和已经消耗的资源。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # 上限由运行配置提供。
    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    max_runtime_seconds: int = Field(gt=0)
    max_estimated_cost_usd: float = Field(ge=0)

    # 已使用量由 Harness Loop 在每轮执行后更新。
    used_steps: int = Field(default=0, ge=0)
    used_tool_calls: int = Field(default=0, ge=0)
    used_model_calls: int = Field(default=0, ge=0)
    used_tokens: int = Field(default=0, ge=0)
    used_runtime_seconds: int = Field(default=0, ge=0)
    used_estimated_cost_usd: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def used_values_must_not_exceed_limits(self) -> Self:
        """阻止 Harness 从无效预算状态继续执行。"""
        if self.used_steps > self.max_steps:
            raise ValueError("used_steps cannot exceed max_steps")
        if self.used_tool_calls > self.max_tool_calls:
            raise ValueError("used_tool_calls cannot exceed max_tool_calls")
        if self.used_model_calls > self.max_model_calls:
            raise ValueError("used_model_calls cannot exceed max_model_calls")
        if self.used_tokens > self.max_tokens:
            raise ValueError("used_tokens cannot exceed max_tokens")
        if self.used_runtime_seconds > self.max_runtime_seconds:
            raise ValueError("used_runtime_seconds cannot exceed max_runtime_seconds")
        if self.used_estimated_cost_usd > self.max_estimated_cost_usd:
            raise ValueError("used_estimated_cost_usd cannot exceed max_estimated_cost_usd")
        return self

    @property
    def remaining_steps(self) -> int:
        """返回剩余可执行步骤数。"""
        return self.max_steps - self.used_steps

    @property
    def remaining_tool_calls(self) -> int:
        """返回剩余工具调用次数。"""
        return self.max_tool_calls - self.used_tool_calls

    @property
    def remaining_model_calls(self) -> int:
        """返回剩余模型调用次数。"""
        return self.max_model_calls - self.used_model_calls

    @property
    def remaining_tokens(self) -> int:
        """返回剩余 Token 预算。"""
        return self.max_tokens - self.used_tokens

    @property
    def remaining_runtime_seconds(self) -> int:
        """返回剩余运行时长预算。"""
        return self.max_runtime_seconds - self.used_runtime_seconds

    @property
    def remaining_estimated_cost_usd(self) -> float:
        """返回剩余估算成本预算。"""
        return self.max_estimated_cost_usd - self.used_estimated_cost_usd


class AgentAction(BaseModel):
    """模型提出、但尚未由 Harness 批准执行的下一步动作。"""

    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    intent: str = Field(min_length=1, max_length=1_000)
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    expected_observation: str | None = Field(default=None, max_length=1_000)
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_tool_metadata(self) -> Self:
        """确保工具元数据只出现在 call_tool 动作中。"""
        if self.action_type is ActionType.CALL_TOOL:
            if not self.tool_name:
                raise ValueError("tool_name is required for call_tool actions")
            return self

        if self.tool_name is not None:
            raise ValueError("tool_name is only allowed for call_tool actions")
        if self.tool_args:
            raise ValueError("tool_args are only allowed for call_tool actions")
        return self


class AgentEvent(BaseModel):
    """记录单个 Harness 步骤产生的可审计事件。"""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    step_id: int = Field(ge=0)
    event_type: EventType
    timestamp: datetime = Field(default_factory=utc_now)
    node: str | None = Field(default=None, max_length=4_000)
    action: AgentAction | None = None
    observation: dict[str, Any] | None = None
    token_usage: dict[str, int] | None = None
    input_summary: str | None = Field(default=None, max_length=4_000)
    latency_ms: int | None = Field(default=None, ge=0)
    decision: str | None = Field(default=None, max_length=2_000)
    error: str | None = Field(default=None, max_length=4_000)


class DiagnosisState(TypedDict):
    """LangGraph 节点间传递的完整诊断状态。"""

    session_id: str
    thread_id: str
    run_id: str
    user_query: str
    conversation: list[dict[str, Any]]

    issue_type: str | None
    service_name: str | None
    severity: str | None

    # Harness 核心状态。
    plan: list[PlanItem]
    plan_version: int
    context_refs: list[str]
    budget: BudgetState
    trajectory: list[AgentEvent]
    progress_status: ProgressStatus | None
    current_action: NotRequired[AgentAction | None]
    policy_decision: NotRequired[PolicyDecision | None]
    terminal_status: NotRequired[HarnessStatus | None]

    # 诊断领域状态
    retrieved_documents: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    hypotheses: list[str]
    missing_information: list[str]
    diagnosis: dict[str, Any] | None
    recommended_actions: list[dict[str, Any]]
    approval_request: dict[str, Any] | None
    ticket: dict[str, Any] | None

    retry_count: int
    question_count: int
    tool_call_count: int
    step_count: int
    errors: list[str]

    # 后续节点可附加的非关键状态字段
    metadata: NotRequired[dict[str, Any]]


__all__ = [
    "ActionType",
    "AgentAction",
    "AgentEvent",
    "BudgetConsumption",
    "BudgetState",
    "DiagnosisState",
    "EventType",
    "HarnessStatus",
    "PlanItem",
    "PlanStatus",
    "PolicyDecision",
    "PolicyOutcome",
    "ProgressStatus",
    "ToolPolicy",
    "ToolRiskLevel",
]
