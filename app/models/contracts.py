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
    TOOL_RETRY = "tool_retry"
    OBSERVATION_RECORDED = "observation_recorded"
    VERIFICATION_FAILED = "verification_failed"
    CONTEXT_COMPRESSED = "context_compressed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    EVIDENCE_COLLECTED = "evidence_collected"
    PLAN_REVISED = "plan_revised"


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
    STALLED = "stalled"
    FAILED = "failed"


class ApprovalDecision(StrEnum):
    """人工审批对待执行动作作出的决定。"""

    APPROVE = "approve"
    REJECT = "reject"


class ReplayMode(StrEnum):
    """运行回放的数据来源。"""

    CACHED = "cached"


class ContextSource(StrEnum):
    """模型上下文条目的来源类型"""

    TASK = "task"
    PLAN = "plan"
    ERROR = "error"
    EVIDENCE = "evidence"
    TOOL_RESULT = "tool_result"


class ContextItem(BaseModel):
    """一条可安全传入模型的最小上下文"""

    model_config = ConfigDict(extra="forbid")

    source: ContextSource
    reference: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8_000)
    priority: int = Field(ge=0, le=100)


class ContextSnapshot(BaseModel):
    """context Manager 输出的首先上下文快照"""

    model_config = ConfigDict(extra="forbid")

    items: list[ContextItem]
    total_chars: int = Field(ge=0)
    truncated: bool


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


class ToolDefinition(BaseModel):
    """工具注册表使用的静态定义。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1_000)
    risk_level: ToolRiskLevel
    required_args: tuple[str, ...] = ()
    allowed_args: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_argument_schema(self) -> Self:
        """保证参数声明没有重复，且必填参数属于允许参数集合。"""
        if len(set(self.required_args)) != len(self.required_args):
            raise ValueError("required_args must not contain duplicates")
        if len(set(self.allowed_args)) != len(self.allowed_args):
            raise ValueError("allowed_args must not contain duplicates")

        missing_allowed_args = set(self.required_args) - set(self.allowed_args)
        if missing_allowed_args:
            name = ", ".join(sorted(missing_allowed_args))
            raise ValueError(f"required_args must be included in allowed_args: {name}")

        return self


class ScenarioLog(BaseModel):
    """固定故障场景中的单挑结构化日志"""

    model_config = ConfigDict(extra="forbid")

    timestamp: str = Field(min_length=1, max_length=100)
    level: str = Field(min_length=1, max_length=20)
    message: str = Field(min_length=1, max_length=4_000)


class IncidentScenario(BaseModel):
    """供模拟诊断工具读取的可复现故障场景。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=100)
    service: str = Field(min_length=1, max_length=100)
    logs: list[ScenarioLog] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    """策略层对候选动作给出的决策结果"""

    model_config = ConfigDict(extra="forbid")

    outcome: PolicyOutcome
    reason: str = Field(min_length=1, max_length=2_000)
    consumption: BudgetConsumption
    violations: tuple[str, ...] = ()


class ProgressAssessment(BaseModel):
    """Progress Verifier 对单轮行动给出的可执行结论"""

    model_config = ConfigDict(extra="forbid")

    status: ProgressStatus
    reason: str = Field(min_length=1, max_length=2_000)
    fingerprint: str | None = Field(default=None, max_length=8_000)
    consecutive_stalls: int = Field(ge=0)
    should_replan: bool = False
    should_stop: bool = False


class KnowledgeDocument(BaseModel):
    """进入 RAG Ingestion Pipeline 的原始知识文档"""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class KnowledgeChunk(BaseModel):
    """可被关键词和向量检索共同使用的稳定知识分块。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=200)
    index: int = Field(default=0)
    content: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class VectorizedChunk(BaseModel):
    """包含预计算向量的稳定知识分块。"""

    model_config = ConfigDict(extra="forbid")

    chunk: KnowledgeChunk
    vector: list[float] = Field(min_length=1)


class RetrievalHit(BaseModel):
    """一次检索返回的带分数与排名的知识分块。"""

    model_config = ConfigDict(extra="forbid")

    chunk: KnowledgeChunk
    score: float = Field(ge=0)
    rank: int = Field(ge=1)


class FusedRetrievalHit(BaseModel):
    """RRF 融合后的知识分块及其来源。"""

    model_config = ConfigDict(extra="forbid")

    chunk: KnowledgeChunk
    score: float = Field(ge=0)
    rank: int = Field(ge=1)
    retriever_names: list[str] = Field(min_length=1)


class EvidenceItem(BaseModel):
    """由一次工具观察结果产生的可引用证据。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=8_000)
    truncated: bool = False


class PlanItem(BaseModel):
    """一个可独立跟踪、可完成或可阻塞的任务计划项。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=1_000)
    status: PlanStatus = PlanStatus.PENDING
    depends_on: list[UUID] = Field(default_factory=list)
    notes: str | None = None


class PlanRevision(BaseModel):
    """一次通过 Harness 校验的完整计划版本。"""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)
    items: list[PlanItem] = Field(min_length=1, max_length=10)
    created_at: datetime = Field(default_factory=utc_now)


class DiagnosisReport(BaseModel):
    """包含可追溯证据引用的结构化诊断报告。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4_000)
    probable_root_cause: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(min_length=1)
    recommended_actions: list[str] = Field(min_length=1)


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
    plan: list[PlanItem] = Field(default_factory=list, max_length=10)

    # 仅 final_answer 可以携带可追溯的最终诊断报告。
    report: DiagnosisReport | None = None

    @model_validator(mode="after")
    def validate_tool_metadata(self) -> Self:
        """约束工具元数据和最终报告只出现在动作。"""
        if self.action_type is ActionType.CALL_TOOL:
            if not self.tool_name:
                raise ValueError("tool_name is required for call_tool actions")
        else:
            if self.tool_name is not None:
                raise ValueError("tool_name is only allowed for call_tool actions")
            if self.tool_args:
                raise ValueError("tool_args are only allowed for call_tool actions")

        if self.action_type is ActionType.FINAL_ANSWER:
            if self.report is None:
                raise ValueError("report is required for final_answer actions")
        elif self.report is not None:
            raise ValueError("report is only allowed for final_answer actions")

        if self.action_type is ActionType.UPDATE_PLAN:
            if not self.plan:
                raise ValueError("plan is required for update_plan actions")
        elif self.plan:
            raise ValueError("plan is only allowed for update_plan actions")

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


class RunSnapshot(BaseModel):
    """一次 Harness 运行结束后的不可变、可重放快照。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    session_id: str = Field(min_length=1, max_length=200)
    thread_id: str = Field(min_length=1, max_length=200)
    terminal_status: HarnessStatus | None

    # 状态不重复保存 trajectory；其中所有值均已转换为 JSON 兼容类型。
    final_state: dict[str, Any]
    trajectory: list[AgentEvent]
    captured_at: datetime = Field(default_factory=utc_now)


class ReplayResult(BaseModel):
    """一次只读回放返回的历史运行结果。"""

    model_config = ConfigDict(extra="forbid")

    source_run_id: UUID
    mode: ReplayMode
    terminal_status: HarnessStatus | None

    # 保留历史最终状态和轨迹，不重新执行任何节点或工具。
    final_state: dict[str, Any]
    trajectory: list[AgentEvent]
    replayed_at: datetime = Field(default_factory=utc_now)


class ApprovalCommand(BaseModel):
    """审批人员提交的决定及其审计理由。"""

    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    reason: str = Field(min_length=1, max_length=2_000)


class ApprovalResolution(BaseModel):
    """一次已记录但尚未执行的审批决议。"""

    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    reason: str = Field(min_length=1, max_length=2_000)
    action: AgentAction
    resolved_at: datetime = Field(default_factory=utc_now)


class EvaluationCheck(BaseModel):
    """一条可解释的轨迹质量检查结果。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    passed: bool
    detail: str = Field(min_length=1, max_length=2_000)


class TrajectoryEvaluation(BaseModel):
    """对单次已归档运行进行的确定性离线评测。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    passed: bool
    score: float = Field(ge=0, le=1)
    checks: list[EvaluationCheck] = Field(min_length=1)
    evaluated_at: datetime = Field(default_factory=utc_now)


class EvaluationCase(BaseModel):
    """一个可重复执行的离线评测样本及其确定性期望。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=100)
    user_query: str = Field(min_length=1, max_length=4_000)
    expected_terminal_status: HarnessStatus
    expected_root_cause_contains: str | None = Field(default=None, max_length=500)
    expected_evidence_tools: tuple[str, ...] = ()


class BenchmarkCaseResult(BaseModel):
    """单个评测样本的轨迹和业务期望检查结果。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=100)
    passed: bool
    score: float = Field(ge=0, le=1)
    checks: list[EvaluationCheck] = Field(min_length=1)
    trajectory_evaluation: TrajectoryEvaluation


class BenchmarkResult(BaseModel):
    """一批离线评测样本的汇总结果。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(ge=0, le=1)
    case_results: list[BenchmarkCaseResult] = Field(min_length=1)
    evaluated_at: datetime = Field(default_factory=utc_now)


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
    plan_history: list[PlanRevision]
    context_refs: list[str]
    budget: BudgetState
    trajectory: list[AgentEvent]
    progress_status: ProgressStatus | None
    current_action: NotRequired[AgentAction | None]
    policy_decision: NotRequired[PolicyDecision | None]
    terminal_status: NotRequired[HarnessStatus | None]
    progress_assessment: NotRequired[ProgressAssessment | None]
    progress_fingerprints: NotRequired[list[str]]
    consecutive_stalls: NotRequired[int]
    replan_requested: NotRequired[bool]
    approval_resolution: NotRequired[ApprovalResolution | None]

    # 诊断领域状态
    retrieved_documents: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    evidence: list[EvidenceItem]
    hypotheses: list[str]
    missing_information: list[str]
    diagnosis: dict[str, Any] | None
    recommended_actions: list[dict[str, Any]]
    approval_request: dict[str, Any] | None
    ticket: dict[str, Any] | None
    diagnosis_report: NotRequired[DiagnosisReport | None]
    final_answer: NotRequired[str | None]

    # Context Manager 在每次动作提出前构建的最小模型上下文
    model_context: NotRequired[ContextSnapshot | None]

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
    "ApprovalCommand",
    "ApprovalDecision",
    "ApprovalResolution",
    "BenchmarkCaseResult",
    "BenchmarkResult",
    "BudgetConsumption",
    "BudgetState",
    "ContextSnapshot",
    "ContextSnapshot",
    "ContextSource",
    "DiagnosisState",
    "DiagnosisState",
    "EvaluationCase",
    "EvaluationCheck",
    "EventType",
    "EvidenceItem",
    "FusedRetrievalHit",
    "HarnessStatus",
    "IncidentScenario",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "PlanItem",
    "PlanRevision",
    "PlanStatus",
    "PolicyDecision",
    "PolicyOutcome",
    "ProgressAssessment",
    "ProgressStatus",
    "ReplayMode",
    "ReplayResult",
    "RetrievalHit",
    "RunSnapshot",
    "ScenarioLog",
    "ToolDefinition",
    "ToolPolicy",
    "ToolRiskLevel",
    "TrajectoryEvaluation",
    "VectorizedChunk",
]
