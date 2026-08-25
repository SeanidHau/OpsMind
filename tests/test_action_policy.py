"""ActionPolicy 的验收测试。"""

from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    PolicyOutcome,
    ToolPolicy,
    ToolRiskLevel,
)


def make_budget(*, used_tool_calls: int = 0) -> BudgetState:
    """构造用于策略测试的预算状态。"""
    return BudgetState(
        max_steps=5,
        max_tool_calls=1,
        max_model_calls=4,
        max_tokens=1_000,
        max_runtime_seconds=60,
        max_estimated_cost_usd=1.0,
        used_tool_calls=used_tool_calls,
    )


def tool_action(name: str, **tool_args: object) -> AgentAction:
    """构造请求调用指定工具的模型动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent=f"调用 {name}",
        tool_name=name,
        tool_args=tool_args,
        reason="收集下一步诊断证据",
    )


def test_unknown_tool_is_blocked() -> None:
    """未注册工具必须在执行前被策略层阻止。"""
    decision = ActionPolicy([]).evaluate(tool_action("unknown_tool"), make_budget())

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.violations == ("tool:not_registered",)


def test_high_risk_tool_requires_approval_without_consuming_budget() -> None:
    """高风险工具需要审批；策略检查本身不能修改预算。"""
    policy = ActionPolicy([ToolPolicy(name="generate_restart_plan", risk_level=ToolRiskLevel.HIGH)])
    budget = make_budget()

    decision = policy.evaluate(tool_action("generate_restart_plan"), budget)

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.consumption.tool_calls == 1
    assert budget.used_tool_calls == 0


def test_low_risk_registered_tool_is_allowed() -> None:
    """有剩余预算时，已注册的低风险只读工具可以执行。"""
    policy = ActionPolicy(
        [
            ToolPolicy(
                name="query_metrics",
                risk_level=ToolRiskLevel.LOW,
                read_only=True,
            )
        ]
    )

    decision = policy.evaluate(tool_action("query_metrics"), make_budget())

    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.consumption.steps == 1
    assert decision.consumption.tool_calls == 1


def test_exhausted_tool_call_budget_blocks_registered_tool() -> None:
    """即使工具已注册，工具调用预算耗尽时也必须阻止执行。"""
    policy = ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)])

    decision = policy.evaluate(tool_action("query_metrics"), make_budget(used_tool_calls=1))

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.violations == ("tool_calls",)


def test_missing_required_argument_is_blocked_before_budget_check() -> None:
    """参数缺失时，策略层应在预算检查前直接阻断动作。"""
    policy = ActionPolicy(
        [
            ToolPolicy(
                name="query_metrics",
                risk_level=ToolRiskLevel.LOW,
                required_args=("service",),
                allowed_args=("service", "window_minutes"),
            )
        ]
    )

    decision = policy.evaluate(tool_action("query_metrics"), make_budget(used_tool_calls=1))

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.reason == "工具参数不符合注册定义。"
    assert decision.violations == ("tool:missing_arg:service",)


def test_unexpected_argument_is_blocked_before_risk_approval() -> None:
    """未声明参数不能通过高风险工具的审批分支。"""
    policy = ActionPolicy(
        [
            ToolPolicy(
                name="generate_restart_plan",
                risk_level=ToolRiskLevel.HIGH,
                allowed_args=("service",),
            )
        ]
    )

    decision = policy.evaluate(
        tool_action("generate_restart_plan", service="payment", region="cn"),
        make_budget(),
    )

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.violations == ("tool:unexpected_arg:region",)


def test_legacy_tool_policy_without_schema_keeps_accepting_arguments() -> None:
    """未声明 schema 的手工 ToolPolicy 必须保持向后兼容。"""
    policy = ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)])

    decision = policy.evaluate(tool_action("query_metrics", unregistered="value"), make_budget())

    assert decision.outcome is PolicyOutcome.ALLOW
