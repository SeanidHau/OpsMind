"""将 Harness Loop 适配为应用可注入的诊断运行服务。"""

from __future__ import annotations

from typing import Protocol

from app.harness.loop import HarnessLoop, create_initial_state
from app.models.contracts import BudgetState, DiagnosisState


class DiagnosisRunner(Protocol):
    """应用路由调用的异步诊断运行接口。"""

    async def run(
        self,
        *,
        session_id: str,
        thread_id: str,
        user_query: str,
    ) -> DiagnosisState:
        """运行单次诊断并返回最终或暂停状态。"""


class HarnessDiagnosisRunner:
    """使用固定预算模板创建新状态并委托给 Harness Loop。"""

    def __init__(self, *, harness_loop: HarnessLoop, budget_template: BudgetState) -> None:
        """绑定已经装配完成的 Harness 与未消耗的预算模板。"""
        if any(
            (
                budget_template.used_steps,
                budget_template.used_tool_calls,
                budget_template.used_model_calls,
                budget_template.used_tokens,
                budget_template.used_runtime_seconds,
                budget_template.used_estimated_cost_usd,
            )
        ):
            raise ValueError("budget_template must not contain consumed budget")

        self._harness_loop = harness_loop
        self._budget_template = budget_template

    async def run(
        self,
        *,
        session_id: str,
        thread_id: str,
        user_query: str,
    ) -> DiagnosisState:
        """为本次请求创建独立状态和预算，再运行 Harness。"""
        initial_state = create_initial_state(
            session_id=session_id,
            thread_id=thread_id,
            user_query=user_query,
            # 深拷贝避免一次运行累计的消耗影响下一次运行。
            budget=self._budget_template.model_copy(deep=True),
        )
        return await self._harness_loop.run(initial_state)
