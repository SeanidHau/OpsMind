"""Harness 组件消融路径的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.diagnosis.runtime import HarnessProfile
from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    ContextSource,
    HarnessStatus,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation

TOOL_NAME = "query_metrics"
OBSERVATION = {"service": "payment-service", "metrics": {"error_rate": 0.12}}


class CapturingActionProvider:
    """返回固定动作，并记录每轮模型可见上下文。"""

    def __init__(self) -> None:
        self.contexts: list[list[tuple[ContextSource, str]]] = []
        self._actions = deque(
            [
                AgentAction(
                    action_type=ActionType.CALL_TOOL,
                    intent="调用 query_metrics",
                    tool_name=TOOL_NAME,
                    tool_args={"service": "payment-service"},
                    reason="读取错误率以确认异常。",
                ),
                AgentAction(
                    action_type=ActionType.FINAL_ANSWER,
                    intent="输出诊断结论",
                    reason="工具证据已满足诊断要求。",
                    report=report_for_observation(
                        tool_name=TOOL_NAME,
                        observation=OBSERVATION,
                    ),
                ),
            ]
        )

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """记录当前上下文后返回下一条固定动作。"""
        self.contexts.append(
            [(item.source, item.reference) for item in state["model_context"].items]
        )
        return self._actions.popleft()


class FixedToolExecutor:
    """为消融测试提供确定性工具观察结果。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """仅接受预期工具，并返回固定结果。"""
        assert action.tool_name == TOOL_NAME
        return OBSERVATION


def make_state() -> dict[str, Any]:
    """构造足以完成一次工具诊断的最小状态。"""
    return create_initial_state(
        session_id="session-1",
        thread_id="thread-1",
        user_query="支付服务错误率升高",
        budget=BudgetState(
            max_steps=4,
            max_tool_calls=2,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=30,
            max_estimated_cost_usd=0.01,
        ),
    )


def make_loop(provider: CapturingActionProvider, **options: bool) -> HarnessLoop:
    """创建共享预算、策略与工具的受测 Harness。"""
    return HarnessLoop(
        action_provider=provider,  # type: ignore[arg-type]
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.LOW)]),
        **options,
    )


@pytest.mark.asyncio
async def test_without_context_manager_only_exposes_the_user_query() -> None:
    """关闭 Context Manager 后，动作提供器只接收原始任务上下文。"""
    provider = CapturingActionProvider()

    result = await make_loop(provider, use_context_manager=False).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert provider.contexts == [
        [(ContextSource.TASK, "user_query")],
        [(ContextSource.TASK, "user_query")],
    ]


@pytest.mark.asyncio
async def test_without_progress_verifier_skips_progress_assessment() -> None:
    """关闭 Progress Verifier 后，工具成功会直接回到上下文构建节点。"""
    provider = CapturingActionProvider()

    result = await make_loop(provider, use_progress_verifier=False).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result.get("progress_assessment") is None
    assert result["progress_status"] is None


def test_harness_profile_values_are_stable() -> None:
    """命令行和实验输出使用稳定的配置名。"""
    assert [profile.value for profile in HarnessProfile] == [
        "full",
        "without_context_manager",
        "without_progress_verifier",
    ]
