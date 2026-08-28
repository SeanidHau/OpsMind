"""将诊断运行写入 LangSmith 的可选适配器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langsmith import Client
from langsmith.run_helpers import trace, tracing_context

from app.config import Settings
from app.models.contracts import DiagnosisState


class DiagnosisRunTracer(Protocol):
    """记录一次完整诊断操作的边界。"""

    async def trace(
        self,
        *,
        operation: str,
        state: DiagnosisState,
        metadata: dict[str, str],
        execute: Callable[[], Awaitable[DiagnosisState]],
    ) -> DiagnosisState:
        """执行诊断，并在可用时写入根 Trace。"""


class LangSmithDiagnosisRunTracer:
    """为 Harness 运行建立包含 LangGraph 子 Trace 的根 Trace。"""

    def __init__(self, *, api_key: str, project_name: str) -> None:
        self._client = Client(api_key=api_key)
        self._project_name = project_name

    async def trace(
        self,
        *,
        operation: str,
        state: DiagnosisState,
        metadata: dict[str, str],
        execute: Callable[[], Awaitable[DiagnosisState]],
    ) -> DiagnosisState:
        """执行一次运行，并记录安全的输入、输出和关联元数据。"""
        trace_metadata = {
            "operation": operation,
            "run_id": state["run_id"],
            "session_id": state["session_id"],
            "thread_id": state["thread_id"],
            **metadata,
        }
        with tracing_context(
            enabled=True,
            client=self._client,
            project_name=self._project_name,
            tags=["opsmind", "harness", operation],
            metadata=trace_metadata,
        ):
            async with trace(
                "opsmind.harness_run",
                run_type="chain",
                inputs={"user_query": state["user_query"]},
                project_name=self._project_name,
                tags=["opsmind", "harness", operation],
                metadata=trace_metadata,
                client=self._client,
            ) as run:
                result = await execute()
                run.end(outputs=self._outputs(result))
                return result

    @staticmethod
    def _outputs(state: DiagnosisState) -> dict[str, Any]:
        """导出聚合诊断结果，不复制工具原始观察结果。"""
        budget = state["budget"]
        return {
            "terminal_status": state.get("terminal_status"),
            "step_count": state["step_count"],
            "tool_call_count": state["tool_call_count"],
            "model_call_count": budget.used_model_calls,
            "used_tokens": budget.used_tokens,
            "error_count": len(state["errors"]),
        }


def create_langsmith_tracer(settings: Settings) -> DiagnosisRunTracer | None:
    """仅在显式启用且配置密钥后创建 LangSmith 适配器。"""
    if not settings.langsmith_tracing:
        return None
    api_key = settings.langsmith_api_key
    if api_key is None:
        raise ValueError("langsmith_api_key is required when langsmith_tracing is enabled")
    return LangSmithDiagnosisRunTracer(
        api_key=api_key.get_secret_value(),
        project_name=settings.langsmith_project,
    )
