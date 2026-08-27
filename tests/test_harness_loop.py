"""LangGraph Harness Loop 的验收测试。"""

from collections import deque
from typing import Any

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.progress import ProgressVerifier
from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    BudgetState,
    EventType,
    HarnessStatus,
    ProgressStatus,
    ToolPolicy,
    ToolRiskLevel,
)
from tests.support import report_for_observation


class QueueActionProvider:
    """按既定顺序返回动作，替代真实模型以保持测试可重复。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)
        self.received_contexts: list[Any] = []

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一个动作；队列耗尽代表 Loop 意外多执行了一轮。"""
        self.received_contexts.append(state.get("model_context"))
        if not self._actions:
            raise AssertionError("action provider was called after its queue was exhausted")
        return self._actions.popleft()


class RecordingToolExecutor:
    """记录实际调用的工具动作，并返回固定观察结果。"""

    def __init__(self) -> None:
        self.actions: list[AgentAction] = []

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """记录执行动作，供断言策略层是否拦截调用。"""
        self.actions.append(action)
        return {"status": "ok", "tool_name": action.tool_name}


class RecordingEventObserver:
    """记录 Harness 发布的事件，模拟后续实时推送适配器。"""

    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def on_event(self, event: AgentEvent) -> None:
        """保存收到的事件副本。"""
        self.events.append(event)


class MutatingEventObserver:
    """故意修改收到的事件，用于验证 Harness 传递的是副本。"""

    def on_event(self, event: AgentEvent) -> None:
        """修改观察器副本，不应影响运行轨迹。"""
        event.node = "observer_mutated"


class FailingEventObserver:
    """模拟不可用的外部事件消费者。"""

    def on_event(self, event: AgentEvent) -> None:
        """抛出异常，验证 Harness 不会中断。"""
        del event
        raise RuntimeError("event sink is unavailable")


def make_budget(
    *,
    max_steps: int = 5,
    max_tool_calls: int = 3,
    max_model_calls: int = 3,
) -> BudgetState:
    """构造可按需调整步骤、工具和模型调用次数的测试预算。"""
    return BudgetState(
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_model_calls=max_model_calls,
        max_tokens=1_000,
        max_runtime_seconds=60,
        max_estimated_cost_usd=1.0,
    )


def tool_action(name: str, **tool_args: object) -> AgentAction:
    """构造一个由 Loop 处理的工具调用动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent=f"调用 {name}",
        tool_name=name,
        tool_args=tool_args,
        reason="收集故障诊断证据",
    )


def final_action() -> AgentAction:
    """构造正常结束 Loop 的最终回答动作。"""
    return AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出当前诊断结论",
        reason="已经收集到足够证据",
        report=report_for_observation(
            tool_name="query_metrics",
            observation={"status": "ok", "tool_name": "query_metrics"},
        ),
    )


def make_state(*, budget: BudgetState) -> dict[str, Any]:
    """创建具有全部默认领域字段的初始图状态。"""
    return create_initial_state(
        session_id="session-1",
        thread_id="thread-1",
        user_query="支付服务请求超时",
        budget=budget,
    )


@pytest.mark.asyncio
async def test_loop_executes_allowed_tool_then_completes() -> None:
    """低风险工具获准后执行，Loop 随后的最终回答应终止图。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics"), final_action()]),
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.COMPLETED
    assert [action.tool_name for action in executor.actions] == ["query_metrics"]
    assert result["budget"].used_steps == 2
    assert result["budget"].used_tool_calls == 1
    assert result["tool_results"] == [
        {"tool_name": "query_metrics", "result": {"status": "ok", "tool_name": "query_metrics"}}
    ]
    assert EventType.ACTION_PROPOSED in [event.event_type for event in result["trajectory"]]
    assert EventType.TOOL_FINISHED in [event.event_type for event in result["trajectory"]]
    assert result["trajectory"][-2].event_type is EventType.RUN_COMPLETED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_loop_notifies_observer_with_the_complete_event_order() -> None:
    """观察器按创建顺序收到完整轨迹的独立事件副本。"""
    observer = RecordingEventObserver()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics"), final_action()]),
        tool_executor=RecordingToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
        event_observer=observer,
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert [event.event_type for event in observer.events] == [
        event.event_type for event in result["trajectory"]
    ]
    assert observer.events[-1] is not result["trajectory"][-1]


@pytest.mark.asyncio
async def test_loop_observer_cannot_mutate_trajectory_or_interrupt_running() -> None:
    """观察器修改失败都不能改变诊断运行结果。"""
    mutating_loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics"), final_action()]),
        tool_executor=RecordingToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
        event_observer=MutatingEventObserver(),
    )
    failing_loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics"), final_action()]),
        tool_executor=RecordingToolExecutor(),
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
        event_observer=FailingEventObserver(),
    )

    mutated_result = await mutating_loop.run(make_state(budget=make_budget()))
    failed_result = await failing_loop.run(make_state(budget=make_budget()))

    assert all(event.node != "observer_mutated" for event in mutated_result["trajectory"])
    assert mutated_result["terminal_status"] is HarnessStatus.COMPLETED
    assert failed_result["terminal_status"] is HarnessStatus.COMPLETED


@pytest.mark.asyncio
async def test_loop_builds_context_before_requesting_an_action() -> None:
    """动作提供器只能收到 ContextManager 已构建的最小上下文。"""
    provider = QueueActionProvider([final_action()])
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=RecordingToolExecutor(),
        policy=ActionPolicy([]),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert provider.received_contexts[0] is not None
    assert provider.received_contexts[0].items[0].source.value == "task"
    assert result["trajectory"][0].event_type is EventType.CONTEXT_BUILT


@pytest.mark.asyncio
async def test_loop_pauses_for_high_risk_tool_without_executing_it() -> None:
    """高风险工具转入待审批状态，不能消费预算或调用执行器。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("generate_restart_plan")]),
        tool_executor=executor,
        policy=ActionPolicy(
            [
                ToolPolicy(
                    name="generate_restart_plan",
                    risk_level=ToolRiskLevel.HIGH,
                )
            ]
        ),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.WAITING_APPROVAL
    assert result["approval_request"] == {
        "tool_name": "generate_restart_plan",
        "reason": "该工具的风险策略要求人工审批。",
    }
    assert executor.actions == []
    assert result["budget"].used_steps == 0
    assert result["trajectory"][-2].event_type is EventType.RUN_PAUSED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_loop_blocks_unknown_tool_before_execution() -> None:
    """未注册工具必须留下拒绝事件，且执行器完全不可见该动作。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("unknown_tool")]),
        tool_executor=executor,
        policy=ActionPolicy([]),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert executor.actions == []
    assert result["errors"] == ["请求的工具未在当前运行中注册。"]
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_loop_stops_when_next_action_exceeds_budget() -> None:
    """已消费完步骤预算后，下一轮动作必须被阻断而不是无限循环。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics"), final_action()]),
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state(budget=make_budget(max_steps=1)))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert [action.tool_name for action in executor.actions] == ["query_metrics"]
    assert result["budget"].used_steps == 1
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_loop_stops_after_three_repeated_observations() -> None:
    """连续三次无新观察时，Loop 必须终止以避免无效空转。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider([tool_action("query_metrics")] * 4),
        tool_executor=executor,
        policy=ActionPolicy(
            [ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)],
            # 此用例专门验证执行后的停滞逻辑，因此放宽前置重复调用上限。
            max_identical_tool_calls=4,
        ),
        # 本用例验证第三次停滞的强制停止，不触发第 30 阶段的两次停滞 Replan 协议。
        progress_verifier=ProgressVerifier(replan_after_stalls=3, stop_after_stalls=3),
    )

    result = await loop.run(make_state(budget=make_budget(max_tool_calls=5, max_model_calls=5)))

    assert result["terminal_status"] is HarnessStatus.STALLED
    assert result["progress_status"] is ProgressStatus.STALLED
    assert result["consecutive_stalls"] == 3
    assert result["replan_requested"] is True
    assert len(executor.actions) == 4
    assert result["trajectory"][-2].event_type is EventType.VERIFICATION_FAILED
    assert result["trajectory"][-1].event_type is EventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_loop_blocks_duplicate_tool_call_before_execution() -> None:
    """第二次相同调用不能再次执行工具或消耗工具预算。"""
    executor = RecordingToolExecutor()
    repeated_action = tool_action("query_metrics", service="payment")
    loop = HarnessLoop(
        action_provider=QueueActionProvider([repeated_action, repeated_action]),
        tool_executor=executor,
        policy=ActionPolicy([ToolPolicy(name="query_metrics", risk_level=ToolRiskLevel.LOW)]),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert [action.tool_name for action in executor.actions] == ["query_metrics"]
    assert result["budget"].used_tool_calls == 1
    assert result["tool_call_count"] == 1
    assert result["errors"][-1] == "同一工具及参数已成功执行，拒绝重复调用。"
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED


@pytest.mark.asyncio
async def test_loop_blocks_tool_after_its_call_limit_is_reached() -> None:
    """同一工具即使参数不同，也不能超过配置的真实调用上限。"""
    executor = RecordingToolExecutor()
    loop = HarnessLoop(
        action_provider=QueueActionProvider(
            [
                tool_action("query_metrics", service="payment"),
                tool_action("query_metrics", service="order"),
            ]
        ),
        tool_executor=executor,
        policy=ActionPolicy(
            [
                ToolPolicy(
                    name="query_metrics",
                    risk_level=ToolRiskLevel.LOW,
                    max_calls_per_run=1,
                )
            ]
        ),
    )

    result = await loop.run(make_state(budget=make_budget()))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert [action.tool_name for action in executor.actions] == ["query_metrics"]
    assert result["budget"].used_tool_calls == 1
    assert result["tool_call_count"] == 1
    assert result["errors"][-1] == "该工具已达到本次运行的调用上限。"
    assert result["trajectory"][-2].event_type is EventType.ACTION_BLOCKED
