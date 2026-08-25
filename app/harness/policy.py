"""模型动作的预执行策略校验。"""

from collections.abc import Iterable
from typing import Any

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

    def __init__(
        self,
        tool_policies: Iterable[ToolPolicy],
        *,
        max_identical_tool_calls: int = 1,
    ) -> None:
        """初始化工具策略，并配置同一工具参数组合的成功调用上限。"""
        if max_identical_tool_calls < 1:
            raise ValueError("max_identical_tool_calls must be at least 1")

        self._tool_policies = {tool_policy.name: tool_policy for tool_policy in tool_policies}
        # 默认只允许同一工具和参数成功执行一次。
        self._max_identical_tool_calls = max_identical_tool_calls

    def evaluate(
        self,
        action: AgentAction,
        budget: BudgetState,
        *,
        previous_successful_tool_actions: Iterable[AgentAction] = (),
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

            argument_violations = self._validate_tool_args(tool_policy, action.tool_args)
            if argument_violations:
                return PolicyDecision(
                    outcome=PolicyOutcome.BLOCK,
                    reason="工具参数不符合注册定义。",
                    consumption=consumption,
                    violations=argument_violations,
                )

            # 只统计成功调用，工具失败后的自动重试不经过此策略检查。
            if self._has_reached_repeat_limit(
                action,
                previous_successful_tool_actions,
            ):
                return PolicyDecision(
                    outcome=PolicyOutcome.BLOCK,
                    reason="同一工具及参数已成功执行，拒绝重复调用。",
                    consumption=consumption,
                    violations=("tool:duplicate_call",),
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

    @staticmethod
    def _validate_tool_args(
        tool_policy: ToolPolicy,
        tool_args: dict[str, Any],
    ) -> tuple[str, ...]:
        """根据注册表投影的 schema 校验工具参数。"""
        if tool_policy.allowed_args is None:
            return ()

        argument_names = set(tool_args)
        missing_args = sorted(set(tool_policy.required_args) - argument_names)
        unexpected_args = sorted(argument_names - set(tool_policy.allowed_args))

        return (
            *(f"tool:missing_arg:{name}" for name in missing_args),
            *(f"tool:unexpected_arg:{name}" for name in unexpected_args),
        )

    def _has_reached_repeat_limit(
        self,
        action: AgentAction,
        previous_successful_tool_actions: Iterable[AgentAction],
    ) -> bool:
        """统计相同工具和参数的历史成功调用次数。"""
        identical_call_count = sum(
            1
            for previous_action in previous_successful_tool_actions
            if previous_action.action_type is ActionType.CALL_TOOL
            and previous_action.tool_name == action.tool_name
            and previous_action.tool_args == action.tool_args
        )
        return identical_call_count >= self._max_identical_tool_calls
