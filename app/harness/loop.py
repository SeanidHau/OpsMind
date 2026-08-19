"""基于 LangGraph 的最小 Harness Loop。"""

from __future__ import annotations

from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.harness.budget import BudgetManager
from app.harness.policy import ActionPolicy
from app.harness.progress import ProgressVerifier
from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    BudgetState,
    DiagnosisState,
    EventType,
    HarnessStatus,
    PolicyDecision,
    PolicyOutcome,
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
    ) -> None:
        self._action_provider = action_provider
        self._tool_executor = tool_executor
        self._policy = policy
        self._progress_verifier = progress_verifier or ProgressVerifier()
        self._graph = self._build_graph()

    async def run(self, state: DiagnosisState) -> DiagnosisState:
        """运行编译后的 LangGraph，返回最终状态。"""
        result = await self._graph.ainvoke(state)
        return cast(DiagnosisState, result)

    def _build_graph(
        self,
    ) -> CompiledStateGraph[DiagnosisState, None, DiagnosisState, DiagnosisState]:
        """声明节点、边和条件路由，生成可复用的状态图。"""
        graph = StateGraph(DiagnosisState)

        graph.add_node("propose_action", self._propose_action)
        graph.add_node("policy_check", self._policy_check)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_node("verify_progress", self._verify_progress)
        graph.add_node("finish", self._finish)

        graph.add_edge(START, "propose_action")
        graph.add_edge("propose_action", "policy_check")
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
                "verify_progress": "verify_progress",
                "finish": "finish",
            },
        )
        graph.add_conditional_edges(
            "verify_progress",
            self._route_after_verification,
            {
                "propose_action": "propose_action",
                "finish": "finish",
            },
        )
        graph.add_edge("finish", END)

        return graph.compile()

    async def _propose_action(self, state: DiagnosisState) -> dict[str, Any]:
        """调用动作提供器，并将候选动作写入状态和审计轨迹。"""
        action = await self._action_provider.propose_action(state)
        event = self._new_event(
            state,
            event_type=EventType.ACTION_PROPOSED,
            node="propose_action",
            action=action,
            decision=action.reason,
        )

        return {
            "current_action": action,
            "trajectory": [*state["trajectory"], event],
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
            failed_event = self._new_event(
                state,
                event_type=EventType.RUN_FAILED,
                node="execute_tool",
                action=action,
                error=error_text,
            )
            return {
                "terminal_status": HarnessStatus.FAILED,
                "errors": [*state["errors"], error_text],
                "trajectory": [*state["trajectory"], started_event, failed_event],
            }

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
            "tool_results": [*state["tool_results"], observation],
            "tool_call_count": state["tool_call_count"] + 1,
            "trajectory": [
                *state["trajectory"],
                started_event,
                finished_event,
                observation_event,
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

    def _finish(self, state: DiagnosisState) -> dict[str, Any]:
        """为正常结束补充终止状态和完成事件。"""
        if state.get("terminal_status") is not None:
            return {}

        action = self._require_current_action(state)
        if action.action_type is ActionType.FINAL_ANSWER:
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
        """工具失败时结束图；成功时进入进度验证节点。"""
        if state.get("terminal_status") is HarnessStatus.FAILED:
            return "finish"
        return "verify_progress"

    @staticmethod
    def _route_after_verification(state: DiagnosisState) -> str:
        """连续停滞达到上限后结束，否则继续请求下一动作。"""
        if state.get("terminal_status") is HarnessStatus.STALLED:
            return "finish"
        return "propose_action"

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
