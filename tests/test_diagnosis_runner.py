"""Harness 诊断运行服务的隔离与预算测试。"""

from uuid import UUID

import pytest

from app.diagnosis.runner import HarnessDiagnosisRunner
from app.models.contracts import ApprovalCommand, ApprovalDecision, BudgetState, DiagnosisState


class RecordingHarnessLoop:
    """记录服务提交的初始状态，不运行 LangGraph。"""

    def __init__(self) -> None:
        self.states: list[DiagnosisState] = []
        self.resume_calls: list[tuple[UUID, str]] = []
        self.approval_calls: list[tuple[UUID, ApprovalCommand]] = []
        self.approved_resume_calls: list[UUID] = []

    async def run(self, state: DiagnosisState) -> DiagnosisState:
        """保存状态，并原样返回以便检查。"""
        self.states.append(state)
        return state

    async def resume_with_user_input(self, run_id: UUID, answer: str) -> DiagnosisState:
        """记录续跑请求，并返回对应的已保存状态。"""
        self.resume_calls.append((run_id, answer))
        return self.states[0]

    def resolve_approval(self, *, run_id: UUID, command: ApprovalCommand) -> DiagnosisState:
        """记录审批决议，并返回保存状态。"""
        self.approval_calls.append((run_id, command))
        return self.states[0]

    async def resume_approved(self, run_id: UUID) -> DiagnosisState:
        """记录获批续跑，并返回保存状态。"""
        self.approved_resume_calls.append(run_id)
        return self.states[0]


def budget_template() -> BudgetState:
    """返回未消耗的可复用预算模板。"""
    return BudgetState(
        max_steps=6,
        max_tool_calls=3,
        max_model_calls=4,
        max_tokens=2_000,
        max_runtime_seconds=90,
        max_estimated_cost_usd=0.2,
    )


@pytest.mark.asyncio
async def test_harness_runner_creates_isolated_initial_state_and_budget() -> None:
    """每个运行必须获得不同的 run ID 和独立预算副本。"""
    harness_loop = RecordingHarnessLoop()
    runner = HarnessDiagnosisRunner(
        harness_loop=harness_loop,  # type: ignore[arg-type]
        budget_template=budget_template(),
    )

    first = await runner.run(session_id="session-1", thread_id="thread-1", user_query="first")
    second = await runner.run(session_id="session-2", thread_id="thread-2", user_query="second")

    assert first["run_id"] != second["run_id"]
    assert first["budget"] is not second["budget"]
    assert first["budget"] == budget_template()
    assert harness_loop.states == [first, second]


@pytest.mark.asyncio
async def test_harness_runner_delegates_user_input_resume_to_harness() -> None:
    """运行服务不解释用户回答，只将其交给 Harness 恢复 checkpoint。"""
    harness_loop = RecordingHarnessLoop()
    runner = HarnessDiagnosisRunner(
        harness_loop=harness_loop,  # type: ignore[arg-type]
        budget_template=budget_template(),
    )
    state = await runner.run(session_id="session-1", thread_id="thread-1", user_query="first")

    result = await runner.resume_with_user_input(UUID(state["run_id"]), "数据库连接数已达到上限")

    assert result is state
    assert harness_loop.resume_calls == [(UUID(state["run_id"]), "数据库连接数已达到上限")]


@pytest.mark.asyncio
async def test_harness_runner_keeps_approval_and_execution_separate() -> None:
    """记录审批决议不能隐式执行高风险动作。"""
    harness_loop = RecordingHarnessLoop()
    runner = HarnessDiagnosisRunner(
        harness_loop=harness_loop,  # type: ignore[arg-type]
        budget_template=budget_template(),
    )
    state = await runner.run(session_id="session-1", thread_id="thread-1", user_query="first")
    run_id = UUID(state["run_id"])
    command = ApprovalCommand(decision=ApprovalDecision.APPROVE, reason="维护窗口已确认。")

    resolved = runner.resolve_approval(run_id=run_id, command=command)

    assert resolved is state
    assert harness_loop.approval_calls == [(run_id, command)]
    assert harness_loop.approved_resume_calls == []

    resumed = await runner.resume_approved(run_id)

    assert resumed is state
    assert harness_loop.approved_resume_calls == [run_id]


def test_harness_runner_rejects_consumed_budget_template() -> None:
    """已消费预算不能作为新运行模板，避免运行一开始就无预算。"""
    budget = budget_template()
    budget.used_steps = 1

    with pytest.raises(ValueError, match="budget_template must not contain consumed budget"):
        HarnessDiagnosisRunner(
            harness_loop=RecordingHarnessLoop(),  # type: ignore[arg-type]
            budget_template=budget,
        )
