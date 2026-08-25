"""基于 LangGraph 的最小 Harness Loop。"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.harness.approval import ApprovalResolver
from app.harness.budget import BudgetManager
from app.harness.context import ContextManager
from app.harness.evidence import EvidenceCollector, EvidenceGate
from app.harness.model_failure import (
    DefaultModelFailureClassifier,
    ModelFailureClassifier,
)
from app.harness.plan import PlanManager
from app.harness.policy import ActionPolicy
from app.harness.progress import ProgressVerifier
from app.harness.replay import CachedReplayService
from app.harness.report_renderer import MarkdownReportRenderer
from app.harness.restore import RunStateRestorer
from app.harness.snapshot import InMemoryRunArchive, RunArchive, RunSnapshotFactory
from app.harness.tool_failure import (
    DefaultToolFailureClassifier,
    ToolFailureClassifier,
)
from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    ApprovalCommand,
    ApprovalDecision,
    BudgetConsumption,
    BudgetState,
    DiagnosisState,
    EventType,
    HarnessStatus,
    ModelInvocation,
    ModelUsage,
    PlanItem,
    PolicyDecision,
    PolicyOutcome,
    ReplayResult,
)


class ActionProvider(Protocol):
    """为当前状态异步提出下一个结构化动作。"""

    async def propose_action(
        self,
        state: DiagnosisState,
    ) -> AgentAction | ModelInvocation:
        """返回候选动作，必要时同时返回供应商用量。"""


class ToolExecutor(Protocol):
    """执行已经被策略层允许的工具动作。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """执行工具并返回结构化观察结果。"""


def create_initial_state(
    *,
    session_id: str,
    thread_id: str,
    user_query: str,
    budget: BudgetState,
    run_id: UUID | None = None,
) -> DiagnosisState:
    """构造包含所有必填字段的初始图状态。

    状态只通过 LangGraph 节点更新，避免使用模块级可变全局变量。
    """
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "run_id": str(run_id or uuid4()),
        "user_query": user_query,
        "conversation": [],
        "issue_type": None,
        "service_name": None,
        "severity": None,
        "plan": [],
        "plan_version": 0,
        "plan_history": [],
        "replan_requested": False,
        "replan_reason": None,
        "replan_feedback": None,
        "replan_correction_count": 0,
        "context_refs": [],
        "budget": budget,
        "trajectory": [],
        "progress_status": None,
        "retrieved_documents": [],
        "tool_results": [],
        "evidence": [],
        "hypotheses": [],
        "missing_information": [],
        "diagnosis": None,
        "diagnosis_report": None,
        "final_answer": None,
        "recommended_actions": [],
        "approval_request": None,
        "approval_resolution": None,
        "ticket": None,
        "retry_count": 0,
        "question_count": 0,
        "pending_question": None,
        "tool_call_count": 0,
        "step_count": 0,
        "errors": [],
    }


class HarnessLoop:
    """编排动作提出、策略校验、工具执行和中止路由。"""

    def __init__(
        self,
        *,
        action_provider: ActionProvider,
        tool_executor: ToolExecutor,
        policy: ActionPolicy,
        progress_verifier: ProgressVerifier | None = None,
        context_manager: ContextManager | None = None,
        max_tool_retries: int = 2,
        max_model_retries: int = 2,
        model_retry_delay_seconds: float = 0.25,
        model_failure_classifier: ModelFailureClassifier | None = None,
        tool_failure_classifier: ToolFailureClassifier | None = None,
        evidence_collector: EvidenceCollector | None = None,
        evidence_gate: EvidenceGate | None = None,
        report_renderer: MarkdownReportRenderer | None = None,
        run_archive: RunArchive | None = None,
        max_replan_corrections: int = 1,
        max_user_questions: int = 2,
        replan_on_tool_failure: bool = False,
    ) -> None:
        if max_tool_retries < 0:
            raise ValueError("max_tool_retries must not be negative")

        if max_model_retries < 0:
            raise ValueError("max_model_retries must not be negative")
        if model_retry_delay_seconds < 0:
            raise ValueError("model_retry_delay_seconds must not be negative")

        if max_replan_corrections < 0:
            raise ValueError("max_replan_corrections must not be negative")
        if max_user_questions < 0:
            raise ValueError("max_user_questions must not be negative")

        self._action_provider = action_provider
        self._tool_executor = tool_executor
        self._policy = policy
        self._progress_verifier = progress_verifier or ProgressVerifier()
        self._plan_manager = PlanManager()
        self._context_manager = context_manager or ContextManager()
        self._max_tool_retries = max_tool_retries
        self._max_model_retries = max_model_retries
        self._max_replan_corrections = max_replan_corrections
        self._max_user_questions = max_user_questions
        self._replan_on_tool_failure = replan_on_tool_failure
        self._model_retry_delay_seconds = model_retry_delay_seconds
        self._model_failure_classifier = model_failure_classifier or DefaultModelFailureClassifier()
        self._tool_failure_classifier = tool_failure_classifier or DefaultToolFailureClassifier()
        self._evidence_collector = evidence_collector or EvidenceCollector()
        self._evidence_gate = evidence_gate or EvidenceGate()
        self._report_renderer = report_renderer or MarkdownReportRenderer()
        self._run_archive = run_archive or InMemoryRunArchive()
        self._cached_replay = CachedReplayService(self._run_archive)
        self._snapshot_factory = RunSnapshotFactory()
        self._state_restorer = RunStateRestorer()
        self._approval_resolver = ApprovalResolver()
        self._graph = self._build_graph()

    async def run(self, state: DiagnosisState) -> DiagnosisState:
        """运行新任务图，累计墙钟时间预算并保存首次 checkpoint。"""
        return await self._run_and_archive(state, replace_checkpoint=False)

    async def resume_approved(self, run_id: UUID) -> DiagnosisState:
        """从已批准 checkpoint 续跑，累计剩余墙钟时间并替换最新快照。"""
        state = self.restore_checkpoint(run_id)
        resolution = state.get("approval_resolution")

        if resolution is None or resolution.decision not in (
            ApprovalDecision.APPROVE,
            ApprovalDecision.EDIT,
        ):
            raise ValueError("run does not have an approved pending action")
        if state.get("terminal_status") is not None:
            raise ValueError("approved run must not have a terminal status")

        return await self._run_and_archive(state, replace_checkpoint=True)

    async def resume_with_user_input(
        self,
        run_id: UUID,
        answer: str,
    ) -> DiagnosisState:
        """写入用户回答后，从等待输入的 checkpoint 继续运行。"""
        normalized_answer = answer.strip()
        if not normalized_answer:
            raise ValueError("answer must not be blank")

        state = self.restore_checkpoint(run_id)
        if state.get("terminal_status") is not HarnessStatus.WAITING_USER_INPUT:
            raise ValueError("run is not waiting for user input")

        pending_question = state.get("pending_question")
        action = state.get("current_action")
        if pending_question is None or action is None:
            raise ValueError("pending user question is missing")

        resumed_event = self._new_event(
            state,
            event_type=EventType.RUN_RESUMED,
            node="resume_user_input",
            action=action,
            observation={"answer": normalized_answer},
            decision="已接收用户补充信息，继续诊断。",
        )
        resumed_state = cast(
            DiagnosisState,
            {
                **state,
                # 问题与回答都写入会话，后续仅由 Context Manager 按上限暴露。
                "conversation": [
                    *state["conversation"],
                    {"role": "assistant", "content": pending_question},
                    {"role": "user", "content": normalized_answer},
                ],
                "current_action": None,
                "policy_decision": None,
                "pending_question": None,
                "terminal_status": None,
                "trajectory": [*state["trajectory"], resumed_event],
            },
        )
        return await self._run_and_archive(resumed_state, replace_checkpoint=True)

    async def _run_and_archive(
        self,
        state: DiagnosisState,
        *,
        replace_checkpoint: bool,
    ) -> DiagnosisState:
        """在剩余墙钟时间内运行图，并归档最后一个可恢复状态。"""
        started_at = time.monotonic()
        latest_state = state
        remaining_seconds = state["budget"].remaining_runtime_seconds

        try:
            # astream 保留每个已完成节点的状态；超时后仍能安全归档最新状态。
            async with asyncio.timeout(remaining_seconds):
                async for graph_state in self._graph.astream(state, stream_mode="values"):
                    latest_state = cast(DiagnosisState, graph_state)
        except TimeoutError:
            timeout_reason = "本次运行超过时间预算。"
            timeout_event = self._new_event(
                latest_state,
                event_type=EventType.ACTION_BLOCKED,
                node="runtime_budget",
                decision=timeout_reason,
            )
            latest_state = cast(
                DiagnosisState,
                {
                    **latest_state,
                    "terminal_status": HarnessStatus.BLOCKED,
                    "errors": [*latest_state["errors"], timeout_reason],
                    "trajectory": [*latest_state["trajectory"], timeout_event],
                },
            )

        elapsed_seconds = math.ceil(max(0.0, time.monotonic() - started_at))
        consumed_seconds = min(
            elapsed_seconds,
            latest_state["budget"].remaining_runtime_seconds,
        )
        if consumed_seconds > 0:
            latest_state = cast(
                DiagnosisState,
                {
                    **latest_state,
                    # 每次 run/resume 都累计实际墙钟时间，续跑不会重置时间预算。
                    "budget": BudgetManager.consume(
                        latest_state["budget"],
                        BudgetConsumption(runtime_seconds=consumed_seconds),
                    ),
                },
            )

        return self._archive_state(latest_state, replace_checkpoint=replace_checkpoint)

    def _archive_state(
        self,
        state: DiagnosisState,
        *,
        replace_checkpoint: bool,
    ) -> DiagnosisState:
        """追加 checkpoint 事件并保存或替换归档快照。"""
        checkpoint_event = self._new_event(
            state,
            event_type=EventType.CHECKPOINT_SAVED,
            node="run_archive",
            observation={"run_id": state["run_id"]},
            decision="运行快照已保存。",
        )
        state_with_checkpoint = cast(
            DiagnosisState,
            {
                **state,
                "trajectory": [*state["trajectory"], checkpoint_event],
            },
        )

        # 先把 checkpoint 事件写入快照，再归档，保证回放轨迹完整。
        snapshot = self._snapshot_factory.build(state_with_checkpoint)

        if replace_checkpoint:
            self._run_archive.replace(snapshot)
        else:
            self._run_archive.save(snapshot)

        return state_with_checkpoint

    def replay_cached(self, run_id: UUID) -> ReplayResult:
        """只读回放指定运行，不执行模型、工具或 LangGraph 节点。"""
        return self._cached_replay.replay(run_id)

    def restore_checkpoint(self, run_id: UUID) -> DiagnosisState:
        """恢复指定快照的强类型状态，不执行模型、工具或图节点。"""
        snapshot = self._run_archive.load(run_id)
        return self._state_restorer.restore(snapshot)

    def resolve_approval(
        self,
        *,
        run_id: UUID,
        command: ApprovalCommand,
    ) -> DiagnosisState:
        """恢复待审批 checkpoint 并返回已记录审批决议的状态。"""
        state = self.restore_checkpoint(run_id)
        updates = self._approval_resolver.resolve(state=state, command=command)
        resolved_state = cast(DiagnosisState, {**state, **updates})

        # 审批决议本身也必须成为可恢复 checkpoint。
        return self._archive_state(resolved_state, replace_checkpoint=True)

    def _build_graph(
        self,
    ) -> CompiledStateGraph[DiagnosisState, None, DiagnosisState, DiagnosisState]:
        """声明节点、边和条件路由，生成可复用的状态图。"""
        graph = StateGraph(DiagnosisState)

        graph.add_node("propose_action", self._propose_action)
        graph.add_node("apply_plan", self._apply_plan)
        graph.add_node("replan_correction", self._reject_replan_violation)
        graph.add_node("policy_check", self._policy_check)
        graph.add_node("ask_user", self._ask_user)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_node("verify_progress", self._verify_progress)
        graph.add_node("build_context", self._build_context)
        graph.add_node("approve_action", self._approve_action)
        graph.add_node("finish", self._finish)

        graph.add_conditional_edges(
            START,
            self._route_from_start,
            {
                "build_context": "build_context",
                "approve_action": "approve_action",
            },
        )
        graph.add_edge("build_context", "propose_action")
        graph.add_conditional_edges(
            "propose_action",
            self._route_after_proposal,
            {
                "policy_check": "policy_check",
                "replan_correction": "replan_correction",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "replan_correction",
            self._route_after_replan_correction,
            {
                # 首次违规后重建上下文，让模型按协议重新输出。
                "build_context": "build_context",
                # 超过纠正次数上限时，安全结束运行。
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "policy_check",
            self._route_after_policy,
            {
                "apply_plan": "apply_plan",
                "ask_user": "ask_user",
                "execute_tool": "execute_tool",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "apply_plan",
            self._route_after_plan_application,
            {
                # 合法计划才重新构建上下文并继续诊断。
                "build_context": "build_context",
                # 无效计划已被 Harness 阻断，直接结束图。
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "execute_tool",
            self._route_after_tool,
            {
                "retry_tool": "execute_tool",
                "build_context": "build_context",
                "verify_progress": "verify_progress",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "verify_progress",
            self._route_after_verification,
            {
                "build_context": "build_context",
                "propose_action": "propose_action",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "approve_action",
            self._route_after_approved_action,
            {
                "execute_tool": "execute_tool",
                "finish": "finish",
            },
        )
        graph.add_edge("finish", END)

        return graph.compile()

    def _approve_action(self, state: DiagnosisState) -> dict[str, Any]:
        """对已批准动作重新校验工具与预算，再直接执行原工具。"""
        resolution = state.get("approval_resolution")
        action = self._require_current_action(state)

        if resolution is None or resolution.decision not in (
            ApprovalDecision.APPROVE,
            ApprovalDecision.EDIT,
        ):
            raise RuntimeError("approve_action requires an approved resolution")
        if resolution.action != action:
            raise RuntimeError("approved action does not match the current action")

        try:
            updated_plan = self._prepare_plan_item(state, action)
        except ValueError as error:
            error_text = str(error)
            event = self._new_event(
                state,
                event_type=EventType.ACTION_BLOCKED,
                node="approve_action",
                action=action,
                decision=error_text,
            )
            return {
                "terminal_status": HarnessStatus.BLOCKED,
                "errors": [*state["errors"], error_text],
                "trajectory": [*state["trajectory"], event],
            }

        # 审批不绕过工具注册、重复调用检查和预算；只绕过重复的人工作业等待。
        decision = self._policy.evaluate(
            action,
            state["budget"],
            previous_successful_tool_actions=self._successful_tool_actions(state),
            previous_tool_attempts=self._attempted_tool_actions(state),
        )
        if decision.outcome is PolicyOutcome.BLOCK:
            event = self._new_event(
                state,
                event_type=EventType.ACTION_BLOCKED,
                node="approve_action",
                action=action,
                decision=decision.reason,
            )
            return {
                "policy_decision": decision,
                "terminal_status": HarnessStatus.BLOCKED,
                "errors": [*state["errors"], decision.reason],
                "trajectory": [*state["trajectory"], event],
            }

        return {
            "policy_decision": decision,
            "budget": BudgetManager.consume(state["budget"], decision.consumption),
            "step_count": state["step_count"] + 1,
            **({"plan": updated_plan} if updated_plan is not None else {}),
        }

    async def _propose_action(self, state: DiagnosisState) -> dict[str, Any]:
        """在预算边界内调用模型，并对临时失败进行有限重试。"""
        model_consumption = BudgetConsumption(model_calls=1)
        updated_budget = state["budget"]
        trajectory = list(state["trajectory"])
        failure_reasons: list[str] = []

        for attempt in range(self._max_model_retries + 1):
            exceeded = BudgetManager.exceeded_dimensions(
                updated_budget,
                model_consumption,
            )
            if exceeded:
                block_reason = "调用模型会超出本次运行预算。"
                blocked_event = self._new_event(
                    state,
                    event_type=EventType.ACTION_BLOCKED,
                    node="propose_action",
                    decision=block_reason,
                )
                return {
                    "budget": updated_budget,
                    "terminal_status": HarnessStatus.BLOCKED,
                    "errors": [*state["errors"], *failure_reasons, block_reason],
                    "trajectory": [*trajectory, blocked_event],
                }

            # 每一次实际模型请求都先消费一次模型调用预算。
            updated_budget = BudgetManager.consume(updated_budget, model_consumption)

            model_started_at = time.perf_counter()

            try:
                response = await self._action_provider.propose_action(state)
                invocation = self._normalize_model_response(response)
                action = invocation.action
                usage = invocation.usage
            except Exception as error:
                model_latency_ms = self._elapsed_latency_ms(model_started_at)
                model_event = self._new_event(
                    state,
                    event_type=EventType.MODEL_CALLED,
                    node="propose_action",
                    decision=f"第 {attempt + 1} 次模型调用失败。",
                    error=str(error)[:4_000],
                    latency_ms=model_latency_ms,
                )
                failure = self._model_failure_classifier.classify(error)
                failure_reasons.append(failure.message)

                # 错误分类会写入轨迹，便于 replay 和离线评测定位失败来源。
                failure_observation = {
                    "category": failure.category,
                    "retryable": failure.retryable,
                }

                if not failure.retryable:
                    failed_event = self._new_event(
                        state,
                        event_type=EventType.RUN_FAILED,
                        node="propose_action",
                        observation=failure_observation,
                        error=failure.message,
                        decision="模型调用失败，错误分类策略禁止自动重试。",
                    )
                    return {
                        "budget": updated_budget,
                        "terminal_status": HarnessStatus.FAILED,
                        "errors": [*state["errors"], *failure_reasons],
                        "trajectory": [*trajectory, model_event, failed_event],
                    }

                if attempt >= self._max_model_retries:
                    failed_event = self._new_event(
                        state,
                        event_type=EventType.RUN_FAILED,
                        node="propose_action",
                        observation=failure_observation,
                        error=failure.message,
                        decision="可重试模型错误在重试次数耗尽后仍然失败。",
                    )
                    return {
                        "budget": updated_budget,
                        "terminal_status": HarnessStatus.FAILED,
                        "errors": [*state["errors"], *failure_reasons],
                        "trajectory": [*trajectory, model_event, failed_event],
                    }

                retry_event = self._new_event(
                    state,
                    event_type=EventType.MODEL_RETRY,
                    node="propose_action",
                    observation=failure_observation,
                    decision=f"模型调用失败，准备第 {attempt + 1} 次重试。",
                    error=failure.message,
                )
                trajectory.extend((model_event, retry_event))

                # 仅临时传输故障会在总运行时限内进行指数退避。
                delay_seconds = self._model_retry_delay_seconds * (2**attempt)
                await asyncio.sleep(delay_seconds)
                continue
            else:
                usage_consumption = BudgetConsumption(
                    tokens=usage.total_tokens,
                    estimated_cost_usd=usage.estimated_cost_usd,
                )
                usage_exceeded = BudgetManager.exceeded_dimensions(
                    updated_budget,
                    usage_consumption,
                )
                model_latency_ms = self._elapsed_latency_ms(model_started_at)
                model_event = self._new_event(
                    state,
                    event_type=EventType.MODEL_CALLED,
                    node="propose_action",
                    decision=f"第 {attempt + 1} 次模型调用已完成。",
                    latency_ms=model_latency_ms,
                    token_usage=usage.to_event_payload(),
                )

                if usage_exceeded:
                    block_reason = "模型实际用量会超出本次运行预算。"
                    blocked_event = self._new_event(
                        state,
                        event_type=EventType.ACTION_BLOCKED,
                        node="propose_action",
                        decision=block_reason,
                        token_usage=usage.to_event_payload(),
                    )
                    return {
                        # 模型调用已发生；超额用量只在事件中审计，不写入受限预算。
                        "budget": updated_budget,
                        "terminal_status": HarnessStatus.BLOCKED,
                        "errors": [*state["errors"], block_reason],
                        "trajectory": [*trajectory, model_event, blocked_event],
                    }

                # 仅在实际用量未超预算时，将 Token 和成本正式计入预算。
                updated_budget = BudgetManager.consume(updated_budget, usage_consumption)
                action_event = self._new_event(
                    state,
                    event_type=EventType.ACTION_PROPOSED,
                    node="propose_action",
                    action=action,
                    decision=action.reason,
                )
                return {
                    "budget": updated_budget,
                    "current_action": action,
                    "trajectory": [*trajectory, model_event, action_event],
                }

        raise RuntimeError("model retry loop exited unexpectedly")

    def _apply_plan(self, state: DiagnosisState) -> dict[str, Any]:
        """校验模型提交的完整计划，并将其保存为新的计划版本。"""
        action = self._require_current_action(state)
        if action.action_type is not ActionType.UPDATE_PLAN:
            raise RuntimeError("apply_plan only accepts update_plan actions")

        next_version = state["plan_version"] + 1
        try:
            revision = self._plan_manager.create_revision(
                items=action.plan,
                version=next_version,
                reason=action.reason,
            )
        except ValueError as error:
            error_text = str(error)
            event = self._new_event(
                state,
                event_type=EventType.ACTION_BLOCKED,
                node="apply_plan",
                action=action,
                decision=error_text,
            )
            return {
                "terminal_status": HarnessStatus.BLOCKED,
                "errors": [*state["errors"], error_text],
                "trajectory": [*state["trajectory"], event],
            }

        event_type = (
            EventType.PLAN_CREATED if state["plan_version"] == 0 else EventType.PLAN_REVISED
        )
        event = self._new_event(
            state,
            event_type=event_type,
            node="apply_plan",
            action=action,
            observation=revision.model_dump(mode="json"),
            decision=revision.reason,
        )

        return {
            # revision 是历史快照；plan 是当前模型上下文和执行路径使用的版本。
            "plan": [item.model_copy(deep=True) for item in revision.items],
            "plan_version": revision.version,
            "plan_history": [*state["plan_history"], revision],
            "replan_requested": False,
            "replan_reason": None,
            "replan_feedback": None,
            "replan_correction_count": 0,
            "trajectory": [*state["trajectory"], event],
        }

    def _reject_replan_violation(self, state: DiagnosisState) -> dict[str, Any]:
        """拒绝 Replan 期间的非计划动作，并限制模型纠正次数。"""
        action = self._require_current_action(state)
        correction_count = state.get("replan_correction_count", 0) + 1
        reason = "连续停滞后必须先提交 update_plan，不能直接执行其他动作。"
        event = self._new_event(
            state,
            event_type=EventType.ACTION_BLOCKED,
            node="replan_correction",
            action=action,
            decision=reason,
        )

        if correction_count > self._max_replan_corrections:
            terminal_reason = "模型未能在规定次数内提交重新规划。"
            return {
                "replan_correction_count": correction_count,
                "replan_feedback": reason,
                "terminal_status": HarnessStatus.BLOCKED,
                "errors": [*state["errors"], terminal_reason],
                "trajectory": [*state["trajectory"], event],
            }

        return {
            # 记录拒绝原因，使下一次模型调用能生成有针对性的 update_plan。
            "replan_correction_count": correction_count,
            "replan_feedback": reason,
            "trajectory": [*state["trajectory"], event],
        }

    def _policy_check(self, state: DiagnosisState) -> dict[str, Any]:
        """在任何工具执行前完成策略检查和预算消费。"""
        action = self._require_current_action(state)
        try:
            updated_plan = self._prepare_plan_item(state, action)
        except ValueError as error:
            error_text = str(error)
            decision = PolicyDecision(
                outcome=PolicyOutcome.BLOCK,
                reason=error_text,
                consumption=BudgetConsumption(),
                violations=("plan:invalid_action",),
            )
            event = self._new_event(
                state,
                event_type=EventType.ACTION_BLOCKED,
                node="policy_check",
                action=action,
                decision=error_text,
            )
            return {
                "policy_decision": decision,
                "terminal_status": HarnessStatus.BLOCKED,
                "errors": [*state["errors"], error_text],
                "trajectory": [*state["trajectory"], event],
            }

        decision = self._policy.evaluate(
            action,
            state["budget"],
            # 只传入成功工具调用，避免阻断工具失败后的自动重试。
            previous_successful_tool_actions=self._successful_tool_actions(state),
            # 包含成功和失败尝试，单工具上限不能被重试绕过。
            previous_tool_attempts=self._attempted_tool_actions(state),
        )

        if (
            decision.outcome is PolicyOutcome.ALLOW
            and action.action_type is ActionType.ASK_USER
            and state["question_count"] >= self._max_user_questions
        ):
            decision = PolicyDecision(
                outcome=PolicyOutcome.BLOCK,
                reason="本次运行已达到澄清追问上限。",
                consumption=decision.consumption,
                violations=("question_limit",),
            )

        if decision.outcome is PolicyOutcome.ALLOW:
            # 只有允许执行的动作才写入新的预算状态。
            updated_budget = BudgetManager.consume(state["budget"], decision.consumption)
            return {
                "policy_decision": decision,
                "budget": updated_budget,
                "step_count": state["step_count"] + 1,
                **({"plan": updated_plan} if updated_plan is not None else {}),
            }

        if decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            event = self._new_event(
                state,
                event_type=EventType.RUN_PAUSED,
                node="policy_check",
                action=action,
                decision=decision.reason,
            )
            return {
                "policy_decision": decision,
                "terminal_status": HarnessStatus.WAITING_APPROVAL,
                "approval_request": {
                    "tool_name": action.tool_name,
                    "reason": decision.reason,
                },
                "trajectory": [*state["trajectory"], event],
            }

        event = self._new_event(
            state,
            event_type=EventType.ACTION_BLOCKED,
            node="policy_check",
            action=action,
            decision=decision.reason,
        )
        return {
            "policy_decision": decision,
            "terminal_status": HarnessStatus.BLOCKED,
            "errors": [*state["errors"], decision.reason],
            "trajectory": [*state["trajectory"], event],
        }

    def _prepare_plan_item(
        self,
        state: DiagnosisState,
        action: AgentAction,
    ) -> list[PlanItem] | None:
        """为绑定计划项的工具或澄清动作准备 in_progress 状态。"""
        if action.plan_item_id is None:
            return None

        previous_tool_attempts = (
            self._attempted_tool_actions(state)
            if action.action_type is ActionType.CALL_TOOL
            else ()
        )
        return self._plan_manager.start_item(
            state["plan"],
            action.plan_item_id,
            previous_tool_attempts=previous_tool_attempts,
        )

    def _ask_user(self, state: DiagnosisState) -> dict[str, Any]:
        """保存问题并暂停运行，等待外部调用 resume_with_user_input。"""
        action = self._require_current_action(state)
        if action.action_type is not ActionType.ASK_USER or action.question is None:
            raise RuntimeError("ask_user node requires an action with question")

        event = self._new_event(
            state,
            event_type=EventType.RUN_PAUSED,
            node="ask_user",
            action=action,
            decision=action.question,
        )
        return {
            "terminal_status": HarnessStatus.WAITING_USER_INPUT,
            "pending_question": action.question,
            "question_count": state["question_count"] + 1,
            "trajectory": [*state["trajectory"], event],
        }

    async def _execute_tool(self, state: DiagnosisState) -> dict[str, Any]:
        """执行已获准的工具动作，并记录开始、结束和观察事件。"""
        action = self._require_current_action(state)
        if action.action_type is not ActionType.CALL_TOOL:
            raise RuntimeError("execute_tool only accepts call_tool actions")

        started_event = self._new_event(
            state,
            event_type=EventType.TOOL_STARTED,
            node="execute_tool",
            action=action,
        )
        # 仅测量真实 ToolExecutor 调用，不包含策略检查和证据处理。
        tool_started_at = time.perf_counter()

        try:
            result = await self._tool_executor.execute(action)
        except Exception as error:
            tool_latency_ms = self._elapsed_latency_ms(tool_started_at)
            failure = self._tool_failure_classifier.classify(error)
            retry_count = state["retry_count"] + 1
            attempted_tool_calls = state["tool_call_count"] + 1
            failure_observation = {
                "category": failure.category,
                "retryable": failure.retryable,
            }

            # 不可恢复错误不应重复调用工具。
            if not failure.retryable:
                failed_event = self._new_event(
                    state,
                    event_type=EventType.RUN_FAILED,
                    node="execute_tool",
                    action=action,
                    observation=failure_observation,
                    decision="工具调用失败，错误分类策略禁止自动重试。",
                    error=failure.message,
                    latency_ms=tool_latency_ms,
                )
                return {
                    "terminal_status": HarnessStatus.FAILED,
                    "retry_count": retry_count,
                    "tool_call_count": attempted_tool_calls,
                    "errors": [*state["errors"], failure.message],
                    "trajectory": [*state["trajectory"], started_event, failed_event],
                }

            if retry_count <= self._max_tool_retries:
                try:
                    self._ensure_plan_tool_attempt_capacity(state, action)
                except ValueError as error:
                    block_reason = str(error)
                    blocked_event = self._new_event(
                        state,
                        event_type=EventType.ACTION_BLOCKED,
                        node="execute_tool",
                        action=action,
                        observation={
                            **failure_observation,
                            "violations": ("plan:tool_attempt_limit",),
                        },
                        decision=block_reason,
                        error=failure.message,
                        latency_ms=tool_latency_ms,
                    )
                    return {
                        "terminal_status": HarnessStatus.BLOCKED,
                        "retry_count": retry_count,
                        "tool_call_count": attempted_tool_calls,
                        "errors": [*state["errors"], block_reason],
                        "trajectory": [*state["trajectory"], started_event, blocked_event],
                    }

                # 当前失败调用已经实际发生，重试前先检查单工具调用上限。
                attempted_actions = (*self._attempted_tool_actions(state), action)
                tool_limit_violations = self._policy.tool_attempt_limit_violations(
                    action,
                    attempted_actions,
                )
                if tool_limit_violations:
                    block_reason = "该工具已达到本次运行的调用上限。"
                    blocked_event = self._new_event(
                        state,
                        event_type=EventType.ACTION_BLOCKED,
                        node="execute_tool",
                        action=action,
                        observation={
                            **failure_observation,
                            "violations": tool_limit_violations,
                        },
                        decision=block_reason,
                        error=failure.message,
                        latency_ms=tool_latency_ms,
                    )
                    return {
                        "terminal_status": HarnessStatus.BLOCKED,
                        "retry_count": retry_count,
                        "tool_call_count": attempted_tool_calls,
                        "errors": [*state["errors"], block_reason],
                        "trajectory": [*state["trajectory"], started_event, blocked_event],
                    }

                # 重试不重新调用模型或策略，但每次真实工具尝试都消费预算。
                retry_consumption = BudgetConsumption(tool_calls=1)
                exceeded = BudgetManager.exceeded_dimensions(
                    state["budget"],
                    retry_consumption,
                )

                if not exceeded:
                    retry_event = self._new_event(
                        state,
                        event_type=EventType.TOOL_RETRY,
                        node="execute_tool",
                        action=action,
                        observation=failure_observation,
                        decision=f"工具调用失败，准备第 {retry_count} 次重试。",
                        error=failure.message,
                        latency_ms=tool_latency_ms,
                    )
                    return {
                        "budget": BudgetManager.consume(
                            state["budget"],
                            retry_consumption,
                        ),
                        "retry_count": retry_count,
                        "tool_call_count": attempted_tool_calls,
                        "trajectory": [*state["trajectory"], started_event, retry_event],
                    }

                block_reason = "工具重试会超出本次运行预算。"
                blocked_event = self._new_event(
                    state,
                    event_type=EventType.ACTION_BLOCKED,
                    node="execute_tool",
                    action=action,
                    observation=failure_observation,
                    decision=block_reason,
                    error=failure.message,
                    latency_ms=tool_latency_ms,
                )
                return {
                    "terminal_status": HarnessStatus.BLOCKED,
                    "retry_count": retry_count,
                    "tool_call_count": attempted_tool_calls,
                    "errors": [*state["errors"], block_reason],
                    "trajectory": [*state["trajectory"], started_event, blocked_event],
                }

            if self._replan_on_tool_failure and failure.fallback_eligible:
                replan_reason = "工具调用在重试耗尽后失败，需要重新规划替代诊断路径。"
                fallback_event = self._new_event(
                    state,
                    event_type=EventType.VERIFICATION_FAILED,
                    node="execute_tool",
                    action=action,
                    observation=failure_observation,
                    decision=replan_reason,
                    error=failure.message,
                    latency_ms=tool_latency_ms,
                )
                return {
                    "retry_count": 0,
                    "tool_call_count": attempted_tool_calls,
                    "errors": [*state["errors"], failure.message],
                    "replan_requested": True,
                    "replan_reason": replan_reason,
                    "replan_feedback": failure.message,
                    "replan_correction_count": 0,
                    "trajectory": [*state["trajectory"], started_event, fallback_event],
                }

            failed_event = self._new_event(
                state,
                event_type=EventType.RUN_FAILED,
                node="execute_tool",
                action=action,
                observation=failure_observation,
                decision="可重试工具错误在重试次数耗尽后仍然失败。",
                error=failure.message,
                latency_ms=tool_latency_ms,
            )
            return {
                "terminal_status": HarnessStatus.FAILED,
                "retry_count": retry_count,
                "tool_call_count": attempted_tool_calls,
                "errors": [*state["errors"], failure.message],
                "trajectory": [*state["trajectory"], started_event, failed_event],
            }

        tool_name = action.tool_name
        if tool_name is None:
            raise RuntimeError("call_tool action requires tool_name")

        # 成功结果会转为可引用证据，供最终报告进行证据校验。
        evidence = self._evidence_collector.collect(
            tool_name=tool_name,
            observation=result,
        )
        updated_evidence = state["evidence"]
        evidence_event = None

        # 相同工具与相同观察结果只保留一条证据。
        if all(item.evidence_id != evidence.evidence_id for item in state["evidence"]):
            updated_evidence = [*state["evidence"], evidence]
            evidence_event = self._new_event(
                state,
                event_type=EventType.EVIDENCE_COLLECTED,
                node="execute_tool",
                action=action,
                observation=evidence.model_dump(mode="json"),
            )

        finished_event = self._new_event(
            state,
            event_type=EventType.TOOL_FINISHED,
            node="execute_tool",
            action=action,
            observation=result,
            # TOOL_FINISHED 记录本次真实工具调用的耗时。
            latency_ms=self._elapsed_latency_ms(tool_started_at),
        )
        observation_event = self._new_event(
            state,
            event_type=EventType.OBSERVATION_RECORDED,
            node="execute_tool",
            action=action,
            observation=result,
        )
        observation = {
            "tool_name": action.tool_name,
            "result": result,
        }

        return {
            # 成功后清零连续重试计数，下一次工具调用重新计算重试次数。
            "retry_count": 0,
            "evidence": updated_evidence,
            "tool_results": [*state["tool_results"], observation],
            "tool_call_count": state["tool_call_count"] + 1,
            **self._complete_plan_item(state, action),
            "trajectory": [
                *state["trajectory"],
                started_event,
                finished_event,
                observation_event,
                *([evidence_event] if evidence_event is not None else []),
            ],
        }

    def _verify_progress(self, state: DiagnosisState) -> dict[str, Any]:
        """根据最近观察结果检测进度、重复调用与停滞。"""
        action = self._require_current_action(state)
        observation = state["tool_results"][-1] if state["tool_results"] else None

        assessment = self._progress_verifier.assess(
            action=action,
            observation=observation,
            previous_fingerprints=state.get("progress_fingerprints", []),
            consecutive_stalls=state.get("consecutive_stalls", 0),
        )

        fingerprints = list(state.get("progress_fingerprints", []))
        if assessment.fingerprint is not None and assessment.fingerprint not in fingerprints:
            fingerprints.append(assessment.fingerprint)

        updates: dict[str, Any] = {
            "progress_assessment": assessment,
            "progress_fingerprints": fingerprints,
            "progress_status": assessment.status,
            "consecutive_stalls": assessment.consecutive_stalls,
        }

        # 第二次连续停滞只发出重规划信号，保留后续动作机会
        if assessment.should_replan:
            updates.update(
                {
                    "replan_requested": True,
                    "replan_reason": assessment.reason,
                    "replan_feedback": None,
                    "replan_correction_count": 0,
                }
            )

        # 第三次连续停滞才真正结束 LangGraph
        if assessment.should_stop:
            event = self._new_event(
                state,
                event_type=EventType.VERIFICATION_FAILED,
                node="verify_progress",
                action=action,
                decision=assessment.reason,
            )
            updates.update(
                {
                    "terminal_status": HarnessStatus.STALLED,
                    "errors": [*state["errors"], assessment.reason],
                    "trajectory": [*state["trajectory"], event],
                }
            )

        return updates

    def _build_context(self, state: DiagnosisState) -> dict[str, Any]:
        """构建最小上下文，再允许动作提供器读取当前状态。"""
        snapshot = self._context_manager.build(state)
        event = self._new_event(
            state,
            event_type=EventType.CONTEXT_BUILT,
            node="build_context",
            observation=snapshot.model_dump(mode="json"),
        )

        return {
            "model_context": snapshot,
            "context_refs": [item.reference for item in snapshot.items],
            "trajectory": [*state["trajectory"], event],
        }

    def _finish(self, state: DiagnosisState) -> dict[str, Any]:
        """为正常结束补充终止状态和完成事件。"""
        if state.get("terminal_status") is not None:
            return {}

        action = self._require_current_action(state)
        if action.action_type is ActionType.FINAL_ANSWER:
            validation_error = self._evidence_gate.validate(state["evidence"])
            if validation_error is not None:
                verification_event = self._new_event(
                    state,
                    event_type=EventType.VERIFICATION_FAILED,
                    node="finish",
                    action=action,
                    decision=validation_error,
                )
                return {
                    "terminal_status": HarnessStatus.BLOCKED,
                    "errors": [*state["errors"], validation_error],
                    "trajectory": [*state["trajectory"], verification_event],
                }

            report = action.report
            if report is None:
                # AgentAction 的契约已禁止这种情况；保留防御性检查以保护图状态。
                raise RuntimeError("final_answer action requires a diagnosis report")

            try:
                rendered_report = self._report_renderer.render(report, state["evidence"])
            except ValueError as error:
                validation_error = str(error)
                verification_event = self._new_event(
                    state,
                    event_type=EventType.VERIFICATION_FAILED,
                    node="finish",
                    action=action,
                    decision=validation_error,
                )
                return {
                    "terminal_status": HarnessStatus.BLOCKED,
                    "errors": [*state["errors"], validation_error],
                    "trajectory": [*state["trajectory"], verification_event],
                }

            event = self._new_event(
                state,
                event_type=EventType.RUN_COMPLETED,
                node="finish",
                action=action,
                decision=action.reason,
            )
            assessment = self._progress_verifier.assess(
                action=action,
                observation=None,
                previous_fingerprints=state.get("progress_fingerprints", []),
                consecutive_stalls=state.get("consecutive_stalls", 0),
            )
            return {
                "progress_assessment": assessment,
                "progress_status": assessment.status,
                "consecutive_stalls": assessment.consecutive_stalls,
                "terminal_status": HarnessStatus.COMPLETED,
                "diagnosis_report": report,
                "diagnosis": report.model_dump(mode="json"),
                "final_answer": rendered_report,
                "trajectory": [*state["trajectory"], event],
            }

        error_text = f"unsupported terminal action: {action.action_type}"
        event = self._new_event(
            state,
            event_type=EventType.RUN_FAILED,
            node="finish",
            action=action,
            error=error_text,
        )
        return {
            "terminal_status": HarnessStatus.FAILED,
            "errors": [*state["errors"], error_text],
            "trajectory": [*state["trajectory"], event],
        }

    @staticmethod
    def _route_from_start(state: DiagnosisState) -> str:
        """已批准且未终止的 checkpoint 直接续跑原工具动作。"""
        resolution = state.get("approval_resolution")
        if (
            state.get("terminal_status") is None
            and resolution is not None
            and resolution.decision in (ApprovalDecision.APPROVE, ApprovalDecision.EDIT)
        ):
            return "approve_action"
        return "build_context"

    @staticmethod
    def _route_after_approved_action(state: DiagnosisState) -> str:
        """批准后仅在预算或工具策略仍允许时执行原工具。"""
        return "finish" if state.get("terminal_status") is not None else "execute_tool"

    @staticmethod
    def _route_after_proposal(state: DiagnosisState) -> str:
        """模型预算阻断时结束；Replan 期间只接受 update_plan。"""
        if state.get("terminal_status") is not None:
            return "finish"

        action = HarnessLoop._require_current_action(state)
        if (
            state.get("replan_requested", False)
            and action.action_type is not ActionType.UPDATE_PLAN
        ):
            return "replan_correction"
        return "policy_check"

    @staticmethod
    def _route_after_replan_correction(state: DiagnosisState) -> str:
        """首次协议违规后允许模型纠正；超限时结束。"""
        return "finish" if state.get("terminal_status") is not None else "build_context"

    @staticmethod
    def _route_after_plan_application(state: DiagnosisState) -> str:
        """无效计划已终止时不再请求模型，合法计划才继续诊断。"""
        return "finish" if state.get("terminal_status") is not None else "build_context"

    def _route_after_policy(self, state: DiagnosisState) -> str:
        """将策略决策映射为计划、工具或终止节点。"""
        decision = self._require_policy_decision(state)
        if decision.outcome is not PolicyOutcome.ALLOW:
            return "finish"

        action = self._require_current_action(state)
        if action.action_type is ActionType.UPDATE_PLAN:
            return "apply_plan"
        if action.action_type is ActionType.ASK_USER:
            return "ask_user"
        if action.action_type is ActionType.CALL_TOOL:
            return "execute_tool"
        return "finish"

    @staticmethod
    def _route_after_tool(state: DiagnosisState) -> str:
        """按工具执行结果选择重试、验证进度或结束运行。"""
        if state.get("terminal_status") is not None:
            return "finish"
        if state.get("replan_requested", False):
            return "build_context"
        if state["retry_count"] > 0:
            return "retry_tool"
        return "verify_progress"

    @staticmethod
    def _route_after_verification(state: DiagnosisState) -> str:
        """连续停滞达到上限后结束，否则重建上下文并继续。"""
        if state.get("terminal_status") is HarnessStatus.STALLED:
            return "finish"
        return "build_context"

    @staticmethod
    def _require_current_action(state: DiagnosisState) -> AgentAction:
        """读取当前动作；图路由错误时快速失败。"""
        action = state.get("current_action")
        if action is None:
            raise RuntimeError("current_action is required before this node")
        return action

    @staticmethod
    def _require_policy_decision(state: DiagnosisState) -> PolicyDecision:
        """读取策略决策；图路由错误时快速失败。"""
        decision = state.get("policy_decision")
        if decision is None:
            raise RuntimeError("policy_decision is required before this node")
        return decision

    @staticmethod
    def _elapsed_latency_ms(started_at: float) -> int:
        """将单调时钟的耗时转换为非负整数毫秒。"""
        # 向上取整，避免实际调用被记录为 0 ms。
        return math.ceil(max(0.0, (time.perf_counter() - started_at) * 1_000))

    @staticmethod
    def _normalize_model_response(
        response: AgentAction | ModelInvocation,
    ) -> ModelInvocation:
        """兼容旧 Provider，并统一转换为带用量的模型结果。"""
        if isinstance(response, ModelInvocation):
            return response

        # 旧 Provider 只返回 AgentAction 时，默认没有供应商用量。
        return ModelInvocation(action=response, usage=ModelUsage())

    @staticmethod
    def _successful_tool_actions(state: DiagnosisState) -> tuple[AgentAction, ...]:
        """从轨迹提取已成功完成的工具动作，供 Policy 检查重复调用。"""
        return tuple(
            event.action
            for event in state["trajectory"]
            if event.event_type is EventType.TOOL_FINISHED
            and event.action is not None
            and event.action.action_type is ActionType.CALL_TOOL
        )

    def _complete_plan_item(
        self,
        state: DiagnosisState,
        action: AgentAction,
    ) -> dict[str, list[PlanItem]]:
        """工具成功后完成绑定计划项；未绑定动作不改变旧计划行为。"""
        if action.plan_item_id is None:
            return {}

        return {"plan": self._plan_manager.complete_item(state["plan"], action.plan_item_id)}

    def _ensure_plan_tool_attempt_capacity(
        self,
        state: DiagnosisState,
        action: AgentAction,
    ) -> None:
        """将当前失败尝试纳入计划项计数，再决定能否重试。"""
        if action.plan_item_id is None:
            return

        attempted_actions = (*self._attempted_tool_actions(state), action)
        self._plan_manager.ensure_tool_attempt_capacity(
            state["plan"],
            action.plan_item_id,
            attempted_actions,
        )

    @staticmethod
    def _attempted_tool_actions(state: DiagnosisState) -> tuple[AgentAction, ...]:
        """从轨迹提取所有真实工具尝试，成功和失败都会计入单工具预算。"""
        return tuple(
            event.action
            for event in state["trajectory"]
            if event.event_type is EventType.TOOL_STARTED
            and event.action is not None
            and event.action.action_type is ActionType.CALL_TOOL
        )

    @staticmethod
    def _new_event(
        state: DiagnosisState,
        *,
        event_type: EventType,
        node: str,
        action: AgentAction | None = None,
        observation: dict[str, Any] | None = None,
        decision: str | None = None,
        error: str | None = None,
        latency_ms: int | None = None,
        token_usage: dict[str, int | float] | None = None,
    ) -> AgentEvent:
        """创建与当前状态关联的统一审计事件。"""
        return AgentEvent(
            run_id=UUID(state["run_id"]),
            step_id=state["step_count"],
            event_type=event_type,
            node=node,
            action=action,
            observation=observation,
            decision=decision,
            error=error,
            latency_ms=latency_ms,
            token_usage=token_usage,
        )
