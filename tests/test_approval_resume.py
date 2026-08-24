"""审批通过后的原动作续跑验收测试。"""

from collections import deque
from typing import Any
from uuid import UUID

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.snapshot import InMemoryRunArchive
from app.models.contracts import (
    ActionType,
    AgentAction,
    ApprovalCommand,
    ApprovalDecision,
    BudgetState,
    EventType,
    HarnessStatus,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation

TOOL_NAME = "generate_restart_plan"
TOOL_OBSERVATION = {"status": "plan_ready", "tool_name": TOOL_NAME}


class QueueActionProvider:
    """按顺序提供动作，并记录模型实际被调用的次数。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.calls = 0

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一条动作；队列耗尽表示续跑错误地重新提议了原动作。"""
        del state
        self.calls += 1
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class RecordingToolExecutor:
    """记录实际执行的获批工具调用。"""

    def __init__(self) -> None:
        self.actions: list[AgentAction] = []

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """保存动作并返回可用于最终报告的确定性观察结果。"""
        self.actions.append(action)
        return TOOL_OBSERVATION


def high_risk_action() -> AgentAction:
    """构造需要人工审批的高风险工具动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="生成支付服务重启方案",
        tool_name=TOOL_NAME,
        reason="需要确认维护窗口后再生成重启方案。",
    )


def final_answer_action() -> AgentAction:
    """构造引用获批工具证据的最终诊断报告。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已得到重启方案并完成证据校验。",
        report=report_for_observation(
            tool_name=TOOL_NAME,
            observation=TOOL_OBSERVATION,
        ),
    )


def make_state(*, max_steps: int = 5) -> dict[str, Any]:
    """构造允许一次工具调用和一次最终回答的初始状态。"""
    return create_initial_state(
        session_id="session-approval-resume",
        thread_id="thread-approval-resume",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=max_steps,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


def make_loop(
    *,
    provider: QueueActionProvider,
    executor: RecordingToolExecutor,
    archive: InMemoryRunArchive,
) -> HarnessLoop:
    """构造使用高风险工具策略的可验收 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.HIGH)]),
        run_archive=archive,
    )


@pytest.mark.asyncio
async def test_approved_checkpoint_executes_original_action_without_reproposal() -> None:
    """批准后从 checkpoint 直接执行原动作，并在后续生成最终回答。"""
    archive = InMemoryRunArchive()
    provider = QueueActionProvider([high_risk_action(), final_answer_action()])
    executor = RecordingToolExecutor()
    loop = make_loop(provider=provider, executor=executor, archive=archive)

    waiting = await loop.run(make_state())  # type: ignore[arg-type]
    run_id = UUID(waiting["run_id"])
    assert waiting["terminal_status"] is HarnessStatus.WAITING_APPROVAL
    assert provider.calls == 1
    assert executor.actions == []

    resolved = loop.resolve_approval(
        run_id=run_id,
        command=ApprovalCommand(
            decision=ApprovalDecision.APPROVE,
            reason="维护窗口已确认。",
        ),
    )
    assert resolved["trajectory"][-2].event_type is EventType.RUN_RESUMED
    assert resolved["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED
    assert executor.actions == []

    result = await loop.resume_approved(run_id)

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.calls == 2
    assert [action.tool_name for action in executor.actions] == [TOOL_NAME]
    assert result["budget"].used_steps == 2
    assert result["budget"].used_tool_calls == 1
    assert result["budget"].used_model_calls == 2
    assert [event.event_type for event in result["trajectory"]].count(
        EventType.ACTION_PROPOSED
    ) == 2
    assert result["trajectory"][-2].event_type is EventType.RUN_COMPLETED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED

    persisted = archive.load(run_id)
    assert persisted.terminal_status is HarnessStatus.COMPLETED
    assert persisted.trajectory[-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_rejected_checkpoint_cannot_resume() -> None:
    """审批拒绝后的 checkpoint 不得再次进入工具执行路径。"""
    archive = InMemoryRunArchive()
    provider = QueueActionProvider([high_risk_action()])
    executor = RecordingToolExecutor()
    loop = make_loop(provider=provider, executor=executor, archive=archive)

    waiting = await loop.run(make_state())  # type: ignore[arg-type]
    run_id = UUID(waiting["run_id"])
    rejected = loop.resolve_approval(
        run_id=run_id,
        command=ApprovalCommand(
            decision=ApprovalDecision.REJECT,
            reason="当前不在维护窗口。",
        ),
    )

    assert rejected["terminal_status"] is HarnessStatus.BLOCKED
    assert rejected["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert rejected["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED
    with pytest.raises(ValueError, match="does not have an approved"):
        await loop.resume_approved(run_id)
    assert executor.actions == []


@pytest.mark.asyncio
async def test_approved_checkpoint_blocks_when_budget_changed() -> None:
    """批准不绕过预算；审批后预算耗尽时，原动作必须被阻断。"""
    archive = InMemoryRunArchive()
    provider = QueueActionProvider([high_risk_action()])
    executor = RecordingToolExecutor()
    loop = make_loop(provider=provider, executor=executor, archive=archive)

    waiting = await loop.run(make_state(max_steps=1))  # type: ignore[arg-type]
    run_id = UUID(waiting["run_id"])
    snapshot = archive.load(run_id)
    snapshot.final_state["budget"]["used_steps"] = 1
    archive.replace(snapshot)

    loop.resolve_approval(
        run_id=run_id,
        command=ApprovalCommand(
            decision=ApprovalDecision.APPROVE,
            reason="审批已通过，但预算已被其他控制面消耗。",
        ),
    )
    result = await loop.resume_approved(run_id)

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["errors"][-1] == "执行该动作会超出本次运行预算。"
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED
    assert provider.calls == 1
    assert executor.actions == []
