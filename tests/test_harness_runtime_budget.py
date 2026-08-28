"""Harness 运行时限预算的验收测试。"""

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
from tests.support import diagnosis_report, report_for_observation

TOOL_NAME = "generate_restart_plan"
TOOL_OBSERVATION = {"status": "plan_ready", "tool_name": TOOL_NAME}


class QueueActionProvider:
    """按固定顺序提供动作，避免测试依赖真实模型。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一条预置动作。"""
        del state
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class StaticToolExecutor:
    """返回可被最终报告引用的确定性工具观察结果。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """模拟获批工具的成功执行。"""
        del action
        return TOOL_OBSERVATION


def make_state(
    *,
    max_runtime_seconds: int = 3,
    used_runtime_seconds: int = 0,
) -> dict[str, Any]:
    """构造具有可控运行时预算的初始状态。"""
    return create_initial_state(
        session_id="session-runtime-budget",
        thread_id="thread-runtime-budget",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=max_runtime_seconds,
            max_estimated_cost_usd=1.0,
            used_runtime_seconds=used_runtime_seconds,
        ),
    )


def final_action() -> AgentAction:
    """构造无需真实工具的最终回答动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="测试运行时预算消费。",
        report=diagnosis_report(),
    )


def high_risk_action() -> AgentAction:
    """构造需要审批、用于验证续跑累计的工具动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="生成支付服务重启方案",
        tool_name=TOOL_NAME,
        reason="需要确认维护窗口后再生成重启方案。",
    )


def final_answer_after_tool() -> AgentAction:
    """构造引用工具观察结果的最终报告。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已得到重启方案并完成证据校验。",
        report=report_for_observation(
            tool_name=TOOL_NAME,
            observation=TOOL_OBSERVATION,
        ),
    )


def make_loop(
    *,
    provider: QueueActionProvider,
    archive: InMemoryRunArchive,
) -> HarnessLoop:
    """构造包含高风险工具策略的测试 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=StaticToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.HIGH)]),
        run_archive=archive,
    )


@pytest.mark.asyncio
async def test_runtime_budget_exhaustion_blocks_and_archives_latest_state() -> None:
    """已耗尽的运行时预算必须阻断图执行，并仍保存可回放快照。"""
    archive = InMemoryRunArchive()
    loop = make_loop(provider=QueueActionProvider([final_action()]), archive=archive)

    result = await loop.run(
        make_state(max_runtime_seconds=1, used_runtime_seconds=1)  # type: ignore[arg-type]
    )
    run_id = UUID(result["run_id"])

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["errors"][-1] == "本次运行超过时间预算。"
    assert result["budget"].used_runtime_seconds == 1
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-2].node == "runtime_budget"
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED
    assert (await archive.load(run_id)).terminal_status is HarnessStatus.BLOCKED


@pytest.mark.asyncio
async def test_completed_graph_consumes_at_least_one_runtime_second() -> None:
    """成功结束的图按向上取整规则消费运行时预算。"""
    archive = InMemoryRunArchive()
    loop = make_loop(provider=QueueActionProvider([final_action()]), archive=archive)

    result = await loop.run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["budget"].used_runtime_seconds == 1


@pytest.mark.asyncio
async def test_approved_resume_accumulates_runtime_budget() -> None:
    """等待人工审批不计时，但 run 与 resume 的执行时长必须累计。"""
    archive = InMemoryRunArchive()
    provider = QueueActionProvider([high_risk_action(), final_answer_after_tool()])
    loop = make_loop(provider=provider, archive=archive)

    waiting = await loop.run(make_state())  # type: ignore[arg-type]
    run_id = UUID(waiting["run_id"])
    assert waiting["terminal_status"] is HarnessStatus.WAITING_APPROVAL
    assert waiting["budget"].used_runtime_seconds == 1

    await loop.resolve_approval(
        run_id=run_id,
        command=ApprovalCommand(
            decision=ApprovalDecision.APPROVE,
            reason="维护窗口已确认。",
        ),
    )
    result = await loop.resume_approved(run_id)

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["budget"].used_runtime_seconds == 2
    assert (await archive.load(run_id)).final_state["budget"]["used_runtime_seconds"] == 2
