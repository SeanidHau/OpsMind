"""将 JSON 化运行快照恢复为可继续处理的 Harness 状态。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from app.models.contracts import (
    AgentAction,
    BudgetState,
    ContextSnapshot,
    DiagnosisReport,
    DiagnosisState,
    EvidenceItem,
    PlanItem,
    PolicyDecision,
    ProgressAssessment,
    ProgressStatus,
    RunSnapshot,
)


class RunStateRestorer:
    """恢复快照中的 Pydantic 契约对象，不执行任何模型或工具节点。"""

    def restore(self, snapshot: RunSnapshot) -> DiagnosisState:
        """深拷贝 JSON 状态并恢复 Harness 后续节点依赖的类型。"""
        state: dict[str, Any] = deepcopy(snapshot.final_state)

        try:
            # 快照元信息优先于 final_state，防止状态字典中的同名值被污染。
            state["session_id"] = snapshot.session_id
            state["thread_id"] = snapshot.thread_id
            state["run_id"] = str(snapshot.run_id)
            state["terminal_status"] = snapshot.terminal_status
            state["trajectory"] = [event.model_copy(deep=True) for event in snapshot.trajectory]

            state["budget"] = BudgetState.model_validate(state["budget"])
            state["plan"] = [PlanItem.model_validate(item) for item in state["plan"]]
            state["evidence"] = [EvidenceItem.model_validate(item) for item in state["evidence"]]

            if state.get("current_action") is not None:
                state["current_action"] = AgentAction.model_validate(state["current_action"])
            if state.get("policy_decision") is not None:
                state["policy_decision"] = PolicyDecision.model_validate(state["policy_decision"])
            if state.get("progress_assessment") is not None:
                state["progress_assessment"] = ProgressAssessment.model_validate(
                    state["progress_assessment"]
                )
            if state.get("model_context") is not None:
                state["model_context"] = ContextSnapshot.model_validate(state["model_context"])
            if state.get("diagnosis_report") is not None:
                state["diagnosis_report"] = DiagnosisReport.model_validate(
                    state["diagnosis_report"]
                )

            progress_status = state.get("progress_status")
            state["progress_status"] = (
                ProgressStatus(progress_status) if progress_status is not None else None
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"snapshot cannot be restored: {error}") from error

        return cast(DiagnosisState, state)
