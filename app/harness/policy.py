"""模型动作的预执行策略校验。"""

from collections.abc import Iterable

from app.harness.budget import BudgetManager
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetConsumption,
    BudgetState,
    PolicyDecision,
    PolicyOutcome,
    ToolPolicy,
    ToolRiskLevel,
)


class ActionPolicy:
    """基于工具注册表、风险等级和预算决定动作是否可执行。"""

    def __init__(self, tool_policies: Iterable[ToolPolicy]) -> None:
        """将工具策略转换为按名称查询的只读注册表。"""
        self._tool_policies = {tool_policy.name: tool_policy for tool_policy in tool_policies}

    def evaluate(
        self,
        action: AgentAction,
        budget: BudgetState,
    ) -> PolicyDecision:
        """评估候选动作，但绝不直接修改预算状态。"""
        consumption = BudgetConsumption(
            # 每个 Harness Loop 候选动作都算作一步
            steps=1,
            # 仅 call_tool 动作会消耗工具调用次数
            tool_calls=1 if action.action_type is ActionType.CALL_TOOL else 0,
        )

        if action.action_type is ActionType.CALL_TOOL:
            tool_policy = self._tool_policies.get(action.tool_name or "")
            if tool_policy is None:
                return PolicyDecision(
                    outcome=PolicyOutcome.BLOCK,
                    reason="请求的工具未在当前运行中注册。",
                    consumption=consumption,
                    violations=("tool:not_registered",),
                )

        # 预算不足时，不应该进入审批或实际执行流程
        exceeded = BudgetManager.exceeded_dimensions(budget, consumption)
        if exceeded:
            return PolicyDecision(
                outcome=PolicyOutcome.BLOCK,
                reason="执行该动作会超出本次运行预算。",
                consumption=consumption,
                violations=exceeded,
            )

        if action.action_type is ActionType.CALL_TOOL:
            tool_policy = self._tool_policies[action.tool_name or ""]
            # 高风险工具始终需要审批；中风险工具按配置决定
            if tool_policy.risk_level is ToolRiskLevel.HIGH or (
                tool_policy.risk_level is ToolRiskLevel.MEDIUM and tool_policy.requires_approval
            ):
                return PolicyDecision(
                    outcome=PolicyOutcome.REQUIRE_APPROVAL,
                    reason="该工具的风险策略要求人工审批。",
                    consumption=consumption,
                )

        return PolicyDecision(
            outcome=PolicyOutcome.ALLOW,
            reason="动作满足当前工具策略与预算约束。",
            consumption=consumption,
        )
