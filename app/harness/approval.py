"""等待审批运行的决议处理。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.models.contracts import (
    ActionType,
    AgentEvent,
    ApprovalCommand,
    ApprovalDecision,
    ApprovalResolution,
    DiagnosisState,
    EventType,
    HarnessStatus,
)


class ApprovalResolver:
    """仅变更审批状态并记录审计事件，不直接执行获批工具。"""

    def resolve(
        self,
        *,
        state: DiagnosisState,
        command: ApprovalCommand,
    ) -> dict[str, Any]:
        """校验待审批状态后生成批准或拒绝的状态更新。"""
        if state.get("terminal_status") is not HarnessStatus.WAITING_APPROVAL:
            raise ValueError("run is not waiting for approval")
        if state.get("approval_request") is None:
            raise ValueError("approval request is missing")

        action = state.get("current_action")
        if action is None or action.action_type is not ActionType.CALL_TOOL:
            raise ValueError("approval requires a pending tool action")

        resolution = ApprovalResolution(
            decision=command.decision,
            reason=command.reason,
            action=action,
        )

        if command.decision is ApprovalDecision.APPROVE:
            event = self._new_event(
                state=state,
                event_type=EventType.RUN_RESUMED,
                action=action,
                decision=command.reason,
            )
            return {
                # 下一阶段将依据该决议直接路由到已获批的工具动作。
                "terminal_status": None,
                "approval_request": None,
                "approval_resolution": resolution,
                "trajectory": [*state["trajectory"], event],
            }

        rejection_reason = f"人工审批已拒绝：{command.reason}"
        event = self._new_event(
            state=state,
            event_type=EventType.ACTION_BLOCKED,
            action=action,
            decision=rejection_reason,
        )
        return {
            "terminal_status": HarnessStatus.BLOCKED,
            "approval_request": None,
            "approval_resolution": resolution,
            "errors": [*state["errors"], rejection_reason],
            "trajectory": [*state["trajectory"], event],
        }

    @staticmethod
    def _new_event(
        *,
        state: DiagnosisState,
        event_type: EventType,
        action: Any,
        decision: str,
    ) -> AgentEvent:
        """创建与原运行关联的审批审计事件。"""
        return AgentEvent(
            run_id=UUID(state["run_id"]),
            step_id=state["step_count"],
            event_type=event_type,
            node="resolve_approval",
            action=action,
            decision=decision,
        )
