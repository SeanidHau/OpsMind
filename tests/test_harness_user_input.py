"""Harness 受控澄清追问与恢复的验收测试。"""

from collections import deque
from typing import Any
from uuid import UUID

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.snapshot import InMemoryRunArchive
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    EventType,
    HarnessStatus,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation

TOOL_NAME = "query_metrics"
TOOL_OBSERVATION = {"status": "ok", "tool_name": TOOL_NAME}


class QueueActionProvider:
    """按固定顺序返回动作，并保存每轮模型可见的上下文。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.contexts: list[Any] = []

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """记录最小上下文，避免模型直接读取完整运行状态。"""
        self.contexts.append(state["model_context"])
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class FixedToolExecutor:
    """返回固定观察结果，供最终报告引用。"""

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """模拟低风险诊断工具。"""
        del action
        return TOOL_OBSERVATION


def ask_action() -> AgentAction:
    """构造需要用户补充时间窗口的澄清动作。"""
    return AgentAction(
        action_type=ActionType.ASK_USER,
        intent="确认故障发生时间",
        question="请提供接口变慢开始的大致时间。",
        reason="当前缺少缩小日志检索范围所需的时间窗口。",
    )


def tool_action() -> AgentAction:
    """构造用户补充信息后的工具调用。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name=TOOL_NAME,
        reason="根据用户提供的时间窗口收集性能证据。",
    )


def final_action() -> AgentAction:
    """构造引用工具证据的最终回答。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结论",
        reason="已经收集到足够证据。",
        report=report_for_observation(
            tool_name=TOOL_NAME,
            observation=TOOL_OBSERVATION,
        ),
    )


def make_state() -> dict[str, Any]:
    """构造可执行一次追问、一次工具调用和一次最终回答的状态。"""
    return create_initial_state(
        session_id="session-user-input",
        thread_id="thread-user-input",
        user_query="支付服务接口变慢",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )


@pytest.mark.asyncio
async def test_harness_pauses_for_user_input_then_resumes_with_context() -> None:
    """用户回答必须可恢复，并只通过 Context Manager 传给下一轮模型。"""
    archive = InMemoryRunArchive()
    provider = QueueActionProvider([ask_action(), tool_action(), final_action()])
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name=TOOL_NAME, risk_level=ToolRiskLevel.LOW)]),
        run_archive=archive,
    )

    waiting = await loop.run(make_state())  # type: ignore[arg-type]
    run_id = UUID(waiting["run_id"])

    assert waiting["terminal_status"] is HarnessStatus.WAITING_USER_INPUT
    assert waiting["pending_question"] == "请提供接口变慢开始的大致时间。"
    assert waiting["question_count"] == 1
    assert waiting["trajectory"][-2].event_type is EventType.RUN_PAUSED

    result = await loop.resume_with_user_input(run_id, "今天 10:15 左右开始变慢。")

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert result["pending_question"] is None
    assert result["conversation"][-1] == {
        "role": "user",
        "content": "今天 10:15 左右开始变慢。",
    }
    assert any(
        item.source.value == "conversation" and "今天 10:15 左右开始变慢。" in item.content
        for item in provider.contexts[1].items
    )
    assert EventType.RUN_RESUMED in [event.event_type for event in result["trajectory"]]


@pytest.mark.asyncio
async def test_harness_blocks_question_beyond_configured_limit() -> None:
    """超过追问上限时，Harness 必须阻断而不是无限等待用户。"""
    archive = InMemoryRunArchive()
    provider = QueueActionProvider([ask_action(), ask_action()])
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([]),
        run_archive=archive,
        max_user_questions=1,
    )

    waiting = await loop.run(make_state())  # type: ignore[arg-type]
    result = await loop.resume_with_user_input(
        UUID(waiting["run_id"]),
        "今天上午开始出现问题。",
    )

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert result["question_count"] == 1
    assert result["errors"][-1] == "本次运行已达到澄清追问上限。"
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED


@pytest.mark.asyncio
async def test_resume_with_user_input_rejects_blank_answer() -> None:
    """空白回答不能覆盖待处理问题或触发新的模型调用。"""
    archive = InMemoryRunArchive()
    provider = QueueActionProvider([ask_action()])
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=FixedToolExecutor(),
        policy=ActionPolicy([]),
        run_archive=archive,
    )

    waiting = await loop.run(make_state())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="answer must not be blank"):
        await loop.resume_with_user_input(UUID(waiting["run_id"]), "   ")
