"""BudgetManager 的验收测试。"""

import pytest

from app.harness.budget import BudgetExceededError, BudgetManager
from app.models.contracts import BudgetConsumption, BudgetState


def make_budget(**overrides: int | float) -> BudgetState:
    """构造具有足够默认预算的测试状态。"""
    values: dict[str, int | float] = {
        "max_steps": 5,
        "max_tool_calls": 3,
        "max_model_calls": 4,
        "max_tokens": 1_000,
        "max_runtime_seconds": 60,
        "max_estimated_cost_usd": 1.0,
    }
    values.update(overrides)
    return BudgetState.model_validate(values)


def test_consumption_returns_a_new_budget_state() -> None:
    """消费预算后返回新状态，不能原地修改输入状态。"""
    budget = make_budget()
    consumption = BudgetConsumption(
        steps=1,
        tool_calls=1,
        model_calls=1,
        tokens=120,
        runtime_seconds=3,
        estimated_cost_usd=0.02,
    )

    updated = BudgetManager.consume(budget, consumption)

    assert budget.used_steps == 0
    assert budget.used_tokens == 0
    assert updated.used_steps == 1
    assert updated.used_tool_calls == 1
    assert updated.used_model_calls == 1
    assert updated.used_tokens == 120
    assert updated.used_runtime_seconds == 3
    assert updated.used_estimated_cost_usd == 0.02


def test_exceeded_dimensions_identifies_every_exhausted_budget() -> None:
    """预算检查需要指出所有超限维度，而不是只报告第一个错误。"""
    budget = make_budget(used_steps=5, used_tool_calls=3)
    consumption = BudgetConsumption(steps=1, tool_calls=1)

    assert BudgetManager.exceeded_dimensions(budget, consumption) == (
        "steps",
        "tool_calls",
    )


def test_consume_raises_when_budget_is_exhausted() -> None:
    """预算超限时不得返回部分更新后的状态。"""
    budget = make_budget(used_tokens=950)
    consumption = BudgetConsumption(tokens=51)

    with pytest.raises(BudgetExceededError, match="tokens"):
        BudgetManager.consume(budget, consumption)
