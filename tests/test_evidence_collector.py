"""EvidenceCollector 与 Harness 证据写入的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.evidence import EvidenceCollector
from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    ContextSource,
    EventType,
    HarnessStatus,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation


class QueueActionProvider:
    """按固定顺序提供动作，避免模型输出影响证据测试。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一动作；队列耗尽代表 Loop 产生意外调用。"""
        del state
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class MetricsExecutor:
    """返回固定指标观察结果的只读工具执行器。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """返回具有嵌套结构的确定性指标结果。"""
        return {
            "service": action.tool_args["service"],
            "metrics": {"error_rate": 0.12, "p95_latency_ms": 2_500.0},
        }


def tool_action() -> AgentAction:
    """构造指标查询动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name="query_metrics",
        tool_args={"service": "payment-service"},
        reason="收集可引用的指标证据",
    )


def final_action() -> AgentAction:
    """构造诊断完成动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="指标证据已经收集完成",
        report=report_for_observation(
            tool_name="query_metrics",
            observation={
                "service": "payment-service",
                "metrics": {"error_rate": 0.12, "p95_latency_ms": 2_500.0},
            },
        ),
    )


def make_state() -> dict[str, Any]:
    """构造可完成一次工具调用的初始 Harness 状态。"""
    return create_initial_state(
        session_id="session-evidence",
        thread_id="thread-evidence",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


def test_collector_normalizes_key_order_into_stable_evidence_id() -> None:
    """字典键顺序不同的同一观察结果必须得到相同证据 ID。"""
    collector = EvidenceCollector()

    first = collector.collect(
        tool_name="query_metrics",
        observation={"service": "payment-service", "metrics": {"error_rate": 0.12}},
    )
    second = collector.collect(
        tool_name="query_metrics",
        observation={"metrics": {"error_rate": 0.12}, "service": "payment-service"},
    )

    assert first.evidence_id == second.evidence_id
    assert first.content == second.content
    assert first.truncated is False


def test_collector_bounds_context_content_without_changing_evidence_id() -> None:
    """超长观察可截断展示内容，但 ID 仍基于完整观察结果。"""
    collector = EvidenceCollector(max_content_chars=20)
    observation = {"message": "x" * 100}

    bounded = collector.collect(tool_name="query_logs", observation=observation)
    unbounded = EvidenceCollector().collect(tool_name="query_logs", observation=observation)

    assert bounded.truncated is True
    assert len(bounded.content) == 20
    assert bounded.evidence_id == unbounded.evidence_id


@pytest.mark.asyncio
async def test_loop_records_evidence_and_exposes_stable_context_reference() -> None:
    """成功工具观察应写入证据状态、轨迹和下一轮模型上下文。"""
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action(), final_action()]),
        tool_executor=MetricsExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state())

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert len(result["evidence"]) == 1
    evidence = result["evidence"][0]
    assert evidence.tool_name == "query_metrics"
    assert EventType.EVIDENCE_COLLECTED in [event.event_type for event in result["trajectory"]]
    assert any(
        item.source is ContextSource.EVIDENCE
        and item.reference == f"evidence:{evidence.evidence_id}"
        for item in result["model_context"].items
    )


def test_collector_rejects_invalid_content_limit() -> None:
    """证据内容上限必须为正数。"""
    with pytest.raises(ValueError, match="max_content_chars"):
        EvidenceCollector(max_content_chars=0)
