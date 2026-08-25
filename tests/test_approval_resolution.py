"""等待审批运行的决议处理验收测试。"""

from uuid import UUID

import pytest

from app.harness.approval import ApprovalResolver
from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.restore import RunStateRestorer
from app.harness.snapshot import InMemoryRunArchive, RunSnapshotFactory
from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    ApprovalCommand,
    ApprovalDecision,
    BudgetState,
    EventType,
    HarnessStatus,
)


def waiting_state() -> dict[str, object]:
    """构造带高风险待执行工具的等待审批状态。"""
    state = create_initial_state(
        session_id="session-approval",
        thread_id="thread-approval",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )
    action = AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="生成支付服务重启方案",
        tool_name="generate_restart_plan",
        reason="需要人工确认高风险操作。",
    )
    state.update(
        {
            "current_action": action,
            "terminal_status": HarnessStatus.WAITING_APPROVAL,
            "approval_request": {
                "tool_name": action.tool_name,
                "reason": "该工具的风险策略要求人工审批。",
            },
            "trajectory": [
                AgentEvent(
                    run_id=UUID(state["run_id"]),
                    step_id=0,
                    event_type=EventType.RUN_PAUSED,
                    action=action,
                )
            ],
        }
    )
    return state


def test_approved_resolution_clears_pause_without_executing_tool() -> None:
    """批准只记录恢复意图，不在决议阶段执行高风险工具。"""
    state = waiting_state()

    updates = ApprovalResolver().resolve(
        state=state,  # type: ignore[arg-type]
        command=ApprovalCommand(
            decision=ApprovalDecision.APPROVE,
            reason="已确认在维护窗口内执行。",
        ),
    )

    assert updates["terminal_status"] is None
    assert updates["approval_request"] is None
    assert updates["approval_resolution"].decision is ApprovalDecision.APPROVE
    assert updates["trajectory"][-1].event_type is EventType.RUN_RESUMED


def test_rejected_resolution_blocks_pending_action() -> None:
    """拒绝审批必须阻断运行并保留拒绝原因。"""
    state = waiting_state()

    updates = ApprovalResolver().resolve(
        state=state,  # type: ignore[arg-type]
        command=ApprovalCommand(
            decision=ApprovalDecision.REJECT,
            reason="当前不在维护窗口。",
        ),
    )

    assert updates["terminal_status"] is HarnessStatus.BLOCKED
    assert updates["errors"][-1] == "人工审批已拒绝：当前不在维护窗口。"
    assert updates["approval_resolution"].decision is ApprovalDecision.REJECT
    assert updates["trajectory"][-1].event_type is EventType.ACTION_BLOCKED


def test_edit_resolution_replaces_only_the_pending_tool_parameters() -> None:
    """编辑审批可更新同一工具的参数，并把有效动作写回状态。"""
    state = waiting_state()
    original_action = state["current_action"]
    assert isinstance(original_action, AgentAction)
    edited_action = original_action.model_copy(
        update={"tool_args": {"service": "payment-service", "strategy": "canary"}}
    )

    updates = ApprovalResolver().resolve(
        state=state,  # type: ignore[arg-type]
        command=ApprovalCommand(
            decision=ApprovalDecision.EDIT,
            reason="将重启策略调整为灰度执行。",
            edited_action=edited_action,
        ),
    )

    assert updates["terminal_status"] is None
    assert updates["current_action"] == edited_action
    assert updates["approval_resolution"].decision is ApprovalDecision.EDIT
    assert updates["approval_resolution"].action == edited_action
    assert updates["trajectory"][-1].action == edited_action


def test_edit_resolution_rejects_a_different_tool() -> None:
    """编辑审批不能把原高风险工具替换为另一种工具。"""
    state = waiting_state()
    replacement_action = AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="创建工单",
        tool_name="create_ticket",
        reason="测试工具替换拦截。",
    )

    with pytest.raises(ValueError, match="preserve the pending tool name"):
        ApprovalResolver().resolve(
            state=state,  # type: ignore[arg-type]
            command=ApprovalCommand(
                decision=ApprovalDecision.EDIT,
                reason="尝试替换工具。",
                edited_action=replacement_action,
            ),
        )


def test_edit_command_requires_a_call_tool_and_forbids_non_edit_payload() -> None:
    """命令契约必须拒绝缺失、非工具或非编辑场景的编辑动作。"""
    with pytest.raises(ValueError, match="edited_action is required"):
        ApprovalCommand(decision=ApprovalDecision.EDIT, reason="缺少编辑动作。")
    with pytest.raises(ValueError, match="edited_action must be a call_tool"):
        ApprovalCommand(
            decision=ApprovalDecision.EDIT,
            reason="编辑动作类型错误。",
            edited_action=AgentAction(
                action_type=ActionType.ASK_USER,
                intent="补充时间窗口",
                question="故障大约从何时开始？",
                reason="这不是工具动作。",
            ),
        )
    with pytest.raises(ValueError, match="only allowed for edit"):
        ApprovalCommand(
            decision=ApprovalDecision.APPROVE,
            reason="普通批准不能携带编辑动作。",
            edited_action=AgentAction(
                action_type=ActionType.CALL_TOOL,
                intent="生成方案",
                tool_name="generate_restart_plan",
                reason="测试契约。",
            ),
        )


def test_resolver_rejects_state_without_pending_approval() -> None:
    """非等待审批状态不能被伪造为审批恢复。"""
    state = waiting_state()
    state["terminal_status"] = HarnessStatus.BLOCKED

    with pytest.raises(ValueError, match="not waiting"):
        ApprovalResolver().resolve(
            state=state,  # type: ignore[arg-type]
            command=ApprovalCommand(
                decision=ApprovalDecision.APPROVE,
                reason="测试。",
            ),
        )


def test_resolution_survives_snapshot_restore() -> None:
    """审批决议写入 checkpoint 后应恢复为强类型对象。"""
    state = waiting_state()
    updates = ApprovalResolver().resolve(
        state=state,  # type: ignore[arg-type]
        command=ApprovalCommand(
            decision=ApprovalDecision.APPROVE,
            reason="维护窗口已确认。",
        ),
    )
    resolved_state = {**state, **updates}

    restored = RunStateRestorer().restore(
        RunSnapshotFactory().build(resolved_state)  # type: ignore[arg-type]
    )

    assert restored["approval_resolution"] is not None
    assert restored["approval_resolution"].decision is ApprovalDecision.APPROVE


class UnusedActionProvider:
    """审批决议不应触发模型调用。"""

    async def propose_action(self, state: object) -> AgentAction:
        """若被调用则说明决议错误启动了图。"""
        del state
        raise AssertionError("resolve_approval must not call the action provider")


class UnusedToolExecutor:
    """审批决议不应触发工具调用。"""

    async def execute(self, action: AgentAction) -> dict[str, object]:
        """若被调用则说明决议错误执行了工具。"""
        del action
        raise AssertionError("resolve_approval must not execute tools")


def test_loop_resolves_archived_approval_without_running_graph() -> None:
    """Loop 入口应从 checkpoint 读取状态并仅写入决议。"""
    state = waiting_state()
    archive = InMemoryRunArchive()
    snapshot = RunSnapshotFactory().build(state)  # type: ignore[arg-type]
    archive.save(snapshot)
    loop = HarnessLoop(
        action_provider=UnusedActionProvider(),
        tool_executor=UnusedToolExecutor(),
        policy=ActionPolicy([]),
        run_archive=archive,
    )

    resolved = loop.resolve_approval(
        run_id=snapshot.run_id,
        command=ApprovalCommand(
            decision=ApprovalDecision.APPROVE,
            reason="确认执行。",
        ),
    )

    assert resolved["terminal_status"] is None
    assert resolved["trajectory"][-2].event_type is EventType.RUN_RESUMED
    assert resolved["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED
    persisted = archive.load(snapshot.run_id)
    assert persisted.trajectory[-1].event_type is EventType.CHECKPOINT_SAVED
