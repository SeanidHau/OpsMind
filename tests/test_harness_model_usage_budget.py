"""Harness 模型 Token 与成本预算的验收测试。"""

from typing import Any

import pytest

from app.harness.evidence import EvidenceCollector
from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    EventType,
    HarnessStatus,
    ModelInvocation,
    ModelUsage,
)
from tests.support import diagnosis_report

OBSERVATION = {"status": "ok", "tool_name": "query_metrics"}


class FixedUsageActionProvider:
    """返回带确定性供应商用量的最终回答。"""

    def __init__(self, invocation: ModelInvocation) -> None:
        self._invocation = invocation
        self.calls = 0

    async def propose_action(self, state: dict[str, Any]) -> ModelInvocation:
        """模拟一次已知 Token 与成本的模型调用。"""
        del state
        self.calls += 1
        return self._invocation


class UnusedToolExecutor:
    """模型用量测试不应进入工具执行路径。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """工具执行表示路由错误。"""
        del action
        raise AssertionError("model usage tests must not execute tools")


def make_invocation(*, input_tokens: int, output_tokens: int, cost: float) -> ModelInvocation:
    """构造引用预置证据的最终回答及其模型用量。"""
    evidence = EvidenceCollector().collect(
        tool_name="query_metrics",
        observation=OBSERVATION,
    )
    return ModelInvocation(
        action=AgentAction(
            action_type=ActionType.FINAL_ANSWER,
            intent="输出诊断结论",
            reason="已具备可追溯证据。",
            report=diagnosis_report(evidence_ids=[evidence.evidence_id]),
        ),
        usage=ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
        ),
    )


def make_state(*, max_tokens: int = 100, max_cost: float = 1.0) -> dict[str, Any]:
    """构造预置证据且可调整用量预算的状态。"""
    state = create_initial_state(
        session_id="session-model-usage",
        thread_id="thread-model-usage",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=max_tokens,
            max_runtime_seconds=60,
            max_estimated_cost_usd=max_cost,
        ),
    )
    state["evidence"] = [
        EvidenceCollector().collect(
            tool_name="query_metrics",
            observation=OBSERVATION,
        )
    ]
    return state


def make_loop(provider: FixedUsageActionProvider) -> HarnessLoop:
    """构造不注册工具的最小 Harness。"""
    return HarnessLoop(
        action_provider=provider,
        tool_executor=UnusedToolExecutor(),
        policy=ActionPolicy([]),
    )


@pytest.mark.asyncio
async def test_loop_consumes_observed_model_tokens_and_cost() -> None:
    """实际模型用量未超限时，Token 和成本必须写入预算及轨迹。"""
    provider = FixedUsageActionProvider(
        make_invocation(input_tokens=12, output_tokens=8, cost=0.02)
    )

    result = await make_loop(provider).run(make_state())  # type: ignore[arg-type]

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["budget"].used_model_calls == 1
    assert result["budget"].used_tokens == 20
    assert result["budget"].used_estimated_cost_usd == pytest.approx(0.02)
    model_event = next(
        event for event in result["trajectory"] if event.event_type is EventType.MODEL_CALLED
    )
    assert model_event.token_usage == {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
        "estimated_cost_usd": 0.02,
    }


@pytest.mark.asyncio
async def test_loop_blocks_action_when_observed_usage_exceeds_budget() -> None:
    """模型响应后的实际超额用量必须阻断动作，并保留审计数据。"""
    provider = FixedUsageActionProvider(make_invocation(input_tokens=8, output_tokens=5, cost=0.02))

    result = await make_loop(provider).run(
        make_state(max_tokens=10)  # type: ignore[arg-type]
    )

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert provider.calls == 1
    assert result["budget"].used_model_calls == 1
    assert result["budget"].used_tokens == 0
    assert result["budget"].used_estimated_cost_usd == 0
    assert result["errors"][-1] == "模型实际用量会超出本次运行预算。"
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-2].token_usage == {
        "input_tokens": 8,
        "output_tokens": 5,
        "total_tokens": 13,
        "estimated_cost_usd": 0.02,
    }


@pytest.mark.asyncio
async def test_loop_blocks_action_when_observed_cost_exceeds_budget() -> None:
    """成本超额也必须阻断动作，即使 Token 预算仍然充足。"""
    provider = FixedUsageActionProvider(make_invocation(input_tokens=8, output_tokens=5, cost=0.02))

    result = await make_loop(provider).run(
        make_state(max_cost=0.01)  # type: ignore[arg-type]
    )

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["budget"].used_model_calls == 1
    assert result["budget"].used_tokens == 0
    assert result["budget"].used_estimated_cost_usd == 0
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
