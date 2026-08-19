"""Harness 运行预算的检查与消费逻辑。"""

from app.models.contracts import BudgetConsumption, BudgetState


class BudgetExceededError(ValueError):
    """候选动作会突破运行预算时抛出。"""


class BudgetManager:
    """集中处理预算检查，确保预算状态只在动作获准后更新。"""

    @staticmethod
    def exceeded_dimensions(
        budget: BudgetState,
        consumption: BudgetConsumption,
    ) -> tuple[str, ...]:
        """返回候选消耗会超限的全部预算维度。

        固定顺序让事件记录、测试和后续观测都具有稳定的输出。
        """
        exceeded: list[str] = []

        if budget.used_steps + consumption.steps > budget.max_steps:
            exceeded.append("steps")
        if budget.used_tool_calls + consumption.tool_calls > budget.max_tool_calls:
            exceeded.append("tool_calls")
        if budget.used_model_calls + consumption.model_calls > budget.max_model_calls:
            exceeded.append("model_calls")
        if budget.used_tokens + consumption.tokens > budget.max_tokens:
            exceeded.append("tokens")
        if budget.used_runtime_seconds + consumption.runtime_seconds > budget.max_runtime_seconds:
            exceeded.append("runtime_seconds")
        if (
            budget.used_estimated_cost_usd + consumption.estimated_cost_usd
            > budget.max_estimated_cost_usd
        ):
            exceeded.append("estimated_cost_usd")

        return tuple(exceeded)

    @classmethod
    def consume(
        cls,
        budget: BudgetState,
        consumption: BudgetConsumption,
    ) -> BudgetState:
        """校验并返回消费后的新预算状态。

        不原地修改输入的 budget，避免 LangGraph 状态在策略检查阶段被意外污染。
        """
        exceeded = cls.exceeded_dimensions(budget, consumption)
        if exceeded:
            dimensions = ", ".join(exceeded)
            raise BudgetExceededError(f"Budget exceeded for: {dimensions}")

        return BudgetState(
            max_steps=budget.max_steps,
            max_tool_calls=budget.max_tool_calls,
            max_model_calls=budget.max_model_calls,
            max_tokens=budget.max_tokens,
            max_runtime_seconds=budget.max_runtime_seconds,
            max_estimated_cost_usd=budget.max_estimated_cost_usd,
            used_steps=budget.used_steps + consumption.steps,
            used_tool_calls=budget.used_tool_calls + consumption.tool_calls,
            used_model_calls=budget.used_model_calls + consumption.model_calls,
            used_tokens=budget.used_tokens + consumption.tokens,
            used_runtime_seconds=budget.used_runtime_seconds + consumption.runtime_seconds,
            used_estimated_cost_usd=budget.used_estimated_cost_usd + consumption.estimated_cost_usd,
        )
