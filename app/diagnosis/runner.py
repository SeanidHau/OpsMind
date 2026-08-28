"""将 Harness Loop 适配为应用可注入的诊断运行服务。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from app.harness.events import HarnessEventObserver
from app.harness.loop import HarnessLoop, create_initial_state
from app.models.contracts import ApprovalCommand, BudgetState, DiagnosisState, ReplayResult
from app.observability.langsmith import DiagnosisRunTracer


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


class DiagnosisRunReader(Protocol):
    """读取已归档诊断运行的最小接口。"""

    async def get_run(self, run_id: UUID) -> ReplayResult:
        """返回缓存快照，不重新执行模型、工具或图节点。"""


class DiagnosisRunResumer(Protocol):
    """向等待输入的诊断运行提交用户回答。"""

    async def resume_with_user_input(self, run_id: UUID, answer: str) -> DiagnosisState:
        """写入用户回答，并从已归档 checkpoint 继续运行。"""


class StreamingDiagnosisRunner(Protocol):
    """运行新诊断并将执行中事件交给请求专属观察器。"""

    async def run_with_event_observer(
        self,
        *,
        session_id: str,
        thread_id: str,
        user_query: str,
        run_id: UUID,
        event_observer: HarnessEventObserver,
    ) -> DiagnosisState:
        """使用指定运行 ID 执行诊断，并发布已提交的轨迹事件。"""


class DiagnosisApprovalResolver(Protocol):
    """记录等待审批运行的人工决议。"""

    async def resolve_approval(self, *, run_id: UUID, command: ApprovalCommand) -> DiagnosisState:
        """保存决议，但不在本步骤执行获批动作。"""


class ApprovedDiagnosisRunResumer(Protocol):
    """续跑已经批准或编辑过动作的诊断运行。"""

    async def resume_approved(self, run_id: UUID) -> DiagnosisState:
        """从已保存的批准决议恢复同一运行。"""


class HarnessDiagnosisRunner:
    """使用固定预算模板创建新状态并委托给 Harness Loop。"""

    def __init__(
        self,
        *,
        harness_loop: HarnessLoop,
        budget_template: BudgetState,
        tracer: DiagnosisRunTracer | None = None,
        trace_metadata: dict[str, str] | None = None,
    ) -> None:
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
        self._tracer = tracer
        self._trace_metadata = trace_metadata or {}

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
        return await self._trace(
            operation="run",
            state=initial_state,
            execute=lambda: self._harness_loop.run(initial_state),
        )

    async def run_with_event_observer(
        self,
        *,
        session_id: str,
        thread_id: str,
        user_query: str,
        run_id: UUID,
        event_observer: HarnessEventObserver,
    ) -> DiagnosisState:
        """为事件流创建指定运行 ID 的独立状态并委托给 Harness。"""
        initial_state = create_initial_state(
            session_id=session_id,
            thread_id=thread_id,
            user_query=user_query,
            # 深拷贝避免一次运行累计的消耗影响下一次运行。
            budget=self._budget_template.model_copy(deep=True),
            run_id=run_id,
        )
        return await self._trace(
            operation="run_with_event_observer",
            state=initial_state,
            execute=lambda: self._harness_loop.run(initial_state, event_observer=event_observer),
        )

    async def get_run(self, run_id: UUID) -> ReplayResult:
        """读取 Harness 缓存的运行快照，不触发新的诊断执行。"""
        return await self._harness_loop.replay_cached(run_id)

    async def resume_with_user_input(self, run_id: UUID, answer: str) -> DiagnosisState:
        """将用户回答交给 Harness，从等待输入 checkpoint 续跑。"""
        if self._tracer is None:
            return await self._harness_loop.resume_with_user_input(run_id, answer)
        state = await self._harness_loop.restore_checkpoint(run_id)
        return await self._trace(
            operation="resume_with_user_input",
            state=state,
            execute=lambda: self._harness_loop.resume_with_user_input(run_id, answer),
        )

    async def resolve_approval(self, *, run_id: UUID, command: ApprovalCommand) -> DiagnosisState:
        """记录审批决议，不在决议步骤执行工具。"""
        if self._tracer is None:
            return await self._harness_loop.resolve_approval(run_id=run_id, command=command)
        state = await self._harness_loop.restore_checkpoint(run_id)
        return await self._trace(
            operation="resolve_approval",
            state=state,
            execute=lambda: self._harness_loop.resolve_approval(run_id=run_id, command=command),
        )

    async def resume_approved(self, run_id: UUID) -> DiagnosisState:
        """从已批准 checkpoint 执行原始或编辑后的工具动作。"""
        if self._tracer is None:
            return await self._harness_loop.resume_approved(run_id)
        state = await self._harness_loop.restore_checkpoint(run_id)
        return await self._trace(
            operation="resume_approved",
            state=state,
            execute=lambda: self._harness_loop.resume_approved(run_id),
        )

    async def _trace(
        self,
        *,
        operation: str,
        state: DiagnosisState,
        execute: Callable[[], Awaitable[DiagnosisState]],
    ) -> DiagnosisState:
        """无追踪器时直连 Harness，避免本地开发额外开销。"""
        if self._tracer is None:
            return await execute()
        return await self._tracer.trace(
            operation=operation,
            state=state,
            metadata=self._trace_metadata,
            execute=execute,
        )
