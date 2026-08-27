"""诊断 Harness 的应用装配函数。"""

from app.diagnosis.runner import HarnessDiagnosisRunner
from app.harness.loop import ActionProvider, HarnessLoop
from app.harness.policy import ActionPolicy
from app.models.contracts import BudgetState
from app.tools.registry import ToolRegistry


def default_budget_template() -> BudgetState:
    """返回单次 HTTP 诊断运行使用的未消耗预算模板。"""
    return BudgetState(
        max_steps=12,
        max_tool_calls=6,
        max_model_calls=8,
        max_tokens=16_000,
        max_runtime_seconds=120,
        max_estimated_cost_usd=0.1,
    )


def create_harness_diagnosis_runner(
    *,
    action_provider: ActionProvider,
    tool_registry: ToolRegistry,
    budget_template: BudgetState | None = None,
) -> HarnessDiagnosisRunner:
    """将动作提供器、受控工具和策略装配为可运行的诊断服务。"""
    harness_loop = HarnessLoop(
        action_provider=action_provider,
        tool_executor=tool_registry,
        policy=ActionPolicy(tool_registry.policies()),
    )
    return HarnessDiagnosisRunner(
        harness_loop=harness_loop,
        budget_template=budget_template or default_budget_template(),
    )
