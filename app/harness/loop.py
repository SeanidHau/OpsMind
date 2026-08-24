"""基于 LangGraph 的最小 Harness Loop。"""

from __future__ import annotations

from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.harness.budget import BudgetManager
from app.harness.context import ContextManager
from app.harness.evidence import EvidenceCollector, EvidenceGate
from app.harness.policy import ActionPolicy
from app.harness.progress import ProgressVerifier
from app.harness.replay import CachedReplayService
from app.harness.report_renderer import MarkdownReportRenderer
from app.harness.restore import RunStateRestorer
from app.harness.snapshot import InMemoryRunArchive, RunArchive, RunSnapshotFactory
from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    BudgetConsumption,
    BudgetState,
    DiagnosisState,
    EventType,
    HarnessStatus,
    PolicyDecision,
    PolicyOutcome,
    ReplayResult,
)


class ActionProvider(Protocol):
    """为当前状态异步提出下一个结构化动作。"""

    async def propose_action(self, state: DiagnosisState) -> AgentAction:
        """返回尚未经过策略校验的候选动作。"""


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
        "ticket": None,
        "retry_count": 0,
        "question_count": 0,
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
        evidence_collector: EvidenceCollector | None = None,
        evidence_gate: EvidenceGate | None = None,
        report_renderer: MarkdownReportRenderer | None = None,
        run_archive: RunArchive | None = None,
    ) -> None:
        if max_tool_retries < 0:
            raise ValueError("max_tool_retries must not be negative")

        self._action_provider = action_provider
        self._tool_executor = tool_executor
        self._policy = policy
        self._progress_verifier = progress_verifier or ProgressVerifier()
        self._context_manager = context_manager or ContextManager()
        self._max_tool_retries = max_tool_retries
        self._evidence_collector = evidence_collector or EvidenceCollector()
        self._evidence_gate = evidence_gate or EvidenceGate()
        self._report_renderer = report_renderer or MarkdownReportRenderer()
        self._run_archive = run_archive or InMemoryRunArchive()
        self._cached_replay = CachedReplayService(self._run_archive)
        self._snapshot_factory = RunSnapshotFactory()
        self._state_restorer = RunStateRestorer()
        self._graph = self._build_graph()

    async def run(self, state: DiagnosisState) -> DiagnosisState:
        """运行图，并将结束状态保存为可重放快照。"""
        result = cast(DiagnosisState, await self._graph.ainvoke(state))

        checkpoint_event = self._new_event(
            result,
            event_type=EventType.CHECKPOINT_SAVED,
            node="run_archive",
            observation={"run_id": result["run_id"]},
            decision="运行快照已保存。",
        )
        result_with_checkpoint = cast(
            DiagnosisState,
            {
                **result,
                "trajectory": [*result["trajectory"], checkpoint_event],
            },
        )

        # 先把 checkpoint 事件写入快照，再归档，保证回放轨迹完整。
        snapshot = self._snapshot_factory.build(result_with_checkpoint)
        self._run_archive.save(snapshot)
        return result_with_checkpoint

    def replay_cached(self, run_id: UUID) -> ReplayResult:
        """只读回放指定运行，不执行模型、工具或 LangGraph 节点。"""
        return self._cached_replay.replay(run_id)

    def restore_checkpoint(self, run_id: UUID) -> DiagnosisState:
        """恢复指定快照的强类型状态，不执行模型、工具或图节点。"""
        snapshot = self._run_archive.load(run_id)
        return self._state_restorer.restore(snapshot)

    def _build_graph(
        self,
    ) -> CompiledStateGraph[DiagnosisState, None, DiagnosisState, DiagnosisState]:
        """声明节点、边和条件路由，生成可复用的状态图。"""
        graph = StateGraph(DiagnosisState)

        graph.add_node("propose_action", self._propose_action)
        graph.add_node("policy_check", self._policy_check)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_node("verify_progress", self._verify_progress)
        graph.add_node("build_context", self._build_context)
        graph.add_node("finish", self._finish)

        graph.add_edge(START, "build_context")
        graph.add_edge("build_context", "propose_action")
        graph.add_conditional_edges(
            "propose_action",
            self._route_after_proposal,
            {
                "policy_check": "policy_check",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "policy_check",
            self._route_after_policy,
            {
                "execute_tool": "execute_tool",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "execute_tool",
            self._route_after_tool,
            {
                "retry_tool": "execute_tool",
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
        graph.add_edge("finish", END)

        return graph.compile()

    async def _propose_action(self, state: DiagnosisState) -> dict[str, Any]:
        """在模型预算允许时调用动作提供器，并写入审计轨迹。"""
        model_consumption = BudgetConsumption(model_calls=1)
        exceeded = BudgetManager.exceeded_dimensions(state["budget"], model_consumption)

        if exceeded:
            block_reason = "调用模型会超出本次运行预算。"
            blocked_event = self._new_event(
                state,
                event_type=EventType.ACTION_BLOCKED,
                node="propose_action",
                decision=block_reason,
            )
            return {
                "terminal_status": HarnessStatus.BLOCKED,
                "errors": [*state["errors"], block_reason],
                "trajectory": [*state["trajectory"], blocked_event],
            }

        # 先消费预算，再调用模型，避免模型调用成功后才发现预算不足。
        updated_budget = BudgetManager.consume(state["budget"], model_consumption)
        action = await self._action_provider.propose_action(state)

        model_event = self._new_event(
            state,
            event_type=EventType.MODEL_CALLED,
            node="propose_action",
            decision="模型已返回候选动作。",
        )
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
            "trajectory": [*state["trajectory"], model_event, action_event],
        }

    def _policy_check(self, state: DiagnosisState) -> dict[str, Any]:
        """在任何工具执行前完成策略检查和预算消费。"""
        action = self._require_current_action(state)
        decision = self._policy.evaluate(action, state["budget"])

        if decision.outcome is PolicyOutcome.ALLOW:
            # 只有允许执行的动作才写入新的预算状态。
            updated_budget = BudgetManager.consume(state["budget"], decision.consumption)
            return {
                "policy_decision": decision,
                "budget": updated_budget,
                "step_count": state["step_count"] + 1,
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

        try:
            result = await self._tool_executor.execute(action)
        except Exception as error:
            error_text = str(error)[:4_000]
            retry_count = state["retry_count"] + 1
            attempted_tool_calls = state["tool_call_count"] + 1

            if retry_count <= self._max_tool_retries:
                # 重试不再调用模型或策略，但每一次真实工具尝试必须消耗工具预算。
                retry_consumption = BudgetConsumption(tool_calls=1)
                exceeded = BudgetManager.exceeded_dimensions(state["budget"], retry_consumption)

                if not exceeded:
                    retry_event = self._new_event(
                        state,
                        event_type=EventType.TOOL_RETRY,
                        node="execute_tool",
                        action=action,
                        decision=f"工具调用失败，准备第 {retry_count} 次重试。",
                        error=error_text,
                    )
                    return {
                        "budget": BudgetManager.consume(state["budget"], retry_consumption),
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
                    decision=block_reason,
                    error=error_text,
                )
                return {
                    "terminal_status": HarnessStatus.BLOCKED,
                    "retry_count": retry_count,
                    "tool_call_count": attempted_tool_calls,
                    "errors": [*state["errors"], block_reason],
                    "trajectory": [*state["trajectory"], started_event, blocked_event],
                }

            failed_event = self._new_event(
                state,
                event_type=EventType.RUN_FAILED,
                node="execute_tool",
                action=action,
                error=error_text,
            )
            return {
                "terminal_status": HarnessStatus.FAILED,
                "retry_count": retry_count,
                "tool_call_count": attempted_tool_calls,
                "errors": [*state["errors"], error_text],
                "trajectory": [*state["trajectory"], started_event, failed_event],
            }

        tool_name = action.tool_name
        if tool_name is None:
            raise RuntimeError("call_tool action requires tool_name")

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
            updates["replan_requested"] = True

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
    def _route_after_proposal(state: DiagnosisState) -> str:
        """模型预算阻断时直接结束，否则继续执行动作策略检查。"""
        if state.get("terminal_status") is not None:
            return "finish"
        return "policy_check"

    def _route_after_policy(self, state: DiagnosisState) -> str:
        """将策略决策映射为工具节点或终止节点。"""
        decision = self._require_policy_decision(state)
        if decision.outcome is not PolicyOutcome.ALLOW:
            return "finish"

        action = self._require_current_action(state)
        if action.action_type is ActionType.CALL_TOOL:
            return "execute_tool"
        return "finish"

    @staticmethod
    def _route_after_tool(state: DiagnosisState) -> str:
        """按工具执行结果选择重试、验证进度或结束运行。"""
        if state.get("terminal_status") is not None:
            return "finish"
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
    def _new_event(
        state: DiagnosisState,
        *,
        event_type: EventType,
        node: str,
        action: AgentAction | None = None,
        observation: dict[str, Any] | None = None,
        decision: str | None = None,
        error: str | None = None,
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
        )
