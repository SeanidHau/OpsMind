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

        effective_action = action
        if command.decision is ApprovalDecision.EDIT:
            edited_action = command.edited_action
            if edited_action is None:
                # ApprovalCommand 已校验；保留防御性检查保护直接调用。
                raise RuntimeError("edit decision requires an edited action")
            if edited_action.tool_name != action.tool_name:
                raise ValueError("edited action must preserve the pending tool name")

            # 允许修改参数，但不允许借编辑审批替换高风险工具。
            effective_action = edited_action

        resolution = ApprovalResolution(
            decision=command.decision,
            reason=command.reason,
            action=effective_action,
        )

        if command.decision in (ApprovalDecision.APPROVE, ApprovalDecision.EDIT):
            event = self._new_event(
                state=state,
                event_type=EventType.RUN_RESUMED,
                action=effective_action,
                decision=command.reason,
            )
            return {
                # resume_approved() 将依据决议直接路由到获批的有效工具动作。
                "current_action": effective_action,
                "terminal_status": None,
                "approval_request": None,
                "approval_resolution": resolution,
                "trajectory": [*state["trajectory"], event],
            }

        rejection_reason = f"人工审批已拒绝：{command.reason}"
        event = self._new_event(
            state=state,
            event_type=EventType.ACTION_BLOCKED,
            action=effective_action,
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
