"""Harness 诊断运行服务的隔离与预算测试。"""

import pytest

from app.diagnosis.runner import HarnessDiagnosisRunner
from app.models.contracts import BudgetState, DiagnosisState


class RecordingHarnessLoop:
    """记录服务提交的初始状态，不运行 LangGraph。"""

    def __init__(self) -> None:
        self.states: list[DiagnosisState] = []

    async def run(self, state: DiagnosisState) -> DiagnosisState:
        """保存状态，并原样返回以便检查。"""
        self.states.append(state)
        return state


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


def test_harness_runner_rejects_consumed_budget_template() -> None:
    """已消费预算不能作为新运行模板，避免运行一开始就无预算。"""
    budget = budget_template()
    budget.used_steps = 1

    with pytest.raises(ValueError, match="budget_template must not contain consumed budget"):
        HarnessDiagnosisRunner(
            harness_loop=RecordingHarnessLoop(),  # type: ignore[arg-type]
            budget_template=budget,
        )
