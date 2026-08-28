"""诊断 Harness 的应用装配函数。"""

from enum import StrEnum

from app.diagnosis.runner import HarnessDiagnosisRunner
from app.harness.loop import ActionProvider, HarnessLoop
from app.harness.policy import ActionPolicy
from app.harness.snapshot import RunArchive
from app.models.contracts import BudgetState
from app.observability.langsmith import DiagnosisRunTracer
from app.tools.registry import ToolRegistry


class HarnessProfile(StrEnum):
    """可比较的 Harness 组件组合。"""

    FULL = "full"
    WITHOUT_CONTEXT_MANAGER = "without_context_manager"
    WITHOUT_PROGRESS_VERIFIER = "without_progress_verifier"


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
    run_archive: RunArchive | None = None,
    profile: HarnessProfile = HarnessProfile.FULL,
    tracer: DiagnosisRunTracer | None = None,
) -> HarnessDiagnosisRunner:
    """将动作提供器、受控工具和指定 Harness 组件组合装配为诊断服务。"""
    harness_loop = HarnessLoop(
        action_provider=action_provider,
        tool_executor=tool_registry,
        policy=ActionPolicy(tool_registry.policies()),
        run_archive=run_archive,
        use_context_manager=profile is not HarnessProfile.WITHOUT_CONTEXT_MANAGER,
        use_progress_verifier=profile is not HarnessProfile.WITHOUT_PROGRESS_VERIFIER,
    )
    return HarnessDiagnosisRunner(
        harness_loop=harness_loop,
        budget_template=budget_template or default_budget_template(),
        tracer=tracer,
        trace_metadata={"harness_profile": profile.value},
    )
