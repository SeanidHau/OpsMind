"""cached replay 的验收测试。"""

from collections import deque
from uuid import UUID, uuid4

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.replay import CachedReplayService
from app.harness.snapshot import InMemoryRunArchive, RunSnapshotFactory
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    HarnessStatus,
    ReplayMode,
)
from tests.support import diagnosis_report


def make_state() -> dict[str, object]:
    """构造用于快照和回放的最小状态。"""
    return create_initial_state(
        session_id="session-replay",
        thread_id="thread-replay",
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


def test_cached_replay_returns_independent_historical_copy() -> None:
    """回放必须复用历史状态，但不得让调用方修改归档。"""
    archive = InMemoryRunArchive()
    snapshot = RunSnapshotFactory().build(make_state())  # type: ignore[arg-type]
    archive.save(snapshot)

    replay = CachedReplayService(archive).replay(snapshot.run_id)
    replay.final_state["user_query"] = "回放调用方修改"

    assert replay.source_run_id == snapshot.run_id
    assert replay.mode is ReplayMode.CACHED
    assert archive.load(snapshot.run_id).final_state["user_query"] == "支付服务请求超时"


def test_cached_replay_rejects_unknown_run() -> None:
    """不存在的运行 ID 必须明确失败，不能伪造空回放。"""
    with pytest.raises(KeyError, match="snapshot not found"):
        CachedReplayService(InMemoryRunArchive()).replay(uuid4())


class CountingActionProvider:
    """记录模型动作请求次数。"""

    def __init__(self, action: AgentAction) -> None:
        self._actions = deque([action])
        self.calls = 0

    async def propose_action(self, state: dict[str, object]) -> AgentAction:
        """返回唯一的预设最终回答。"""
        del state
        self.calls += 1
        return self._actions.popleft()


class UnusedToolExecutor:
    """最终回答路径和 cached replay 都不应调用工具。"""

    async def execute(self, action: AgentAction) -> dict[str, object]:
        """若被调用则说明错误地执行了工具。"""
        del action
        raise AssertionError("cached replay must not execute tools")


@pytest.mark.asyncio
async def test_loop_replay_cached_does_not_reinvoke_model_or_tools() -> None:
    """通过 Harness 入口回放时不得重新进入图执行。"""
    archive = InMemoryRunArchive()
    provider = CountingActionProvider(
        AgentAction(
            action_type=ActionType.FINAL_ANSWER,
            intent="输出诊断结论",
            reason="验证只读回放。",
            report=diagnosis_report(),
        )
    )
    loop = HarnessLoop(
        action_provider=provider,
        tool_executor=UnusedToolExecutor(),
        policy=ActionPolicy([]),
        run_archive=archive,
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]
    replay = loop.replay_cached(UUID(result["run_id"]))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert provider.calls == 1
    assert replay.source_run_id == UUID(result["run_id"])
    assert replay.terminal_status is HarnessStatus.BLOCKED
    assert len(replay.trajectory) == len(result["trajectory"])
