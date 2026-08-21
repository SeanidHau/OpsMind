"""运行快照与 Harness 归档的验收测试。"""

from collections import deque
from uuid import UUID, uuid4

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.snapshot import InMemoryRunArchive, RunSnapshotFactory
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    EventType,
    HarnessStatus,
)
from tests.support import diagnosis_report


def make_state() -> dict[str, object]:
    """构造包含 UUID 元数据的最小可快照状态。"""
    state = create_initial_state(
        session_id="session-snapshot",
        thread_id="thread-snapshot",
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
    state["metadata"] = {"request_id": uuid4()}
    return state


def test_snapshot_factory_serializes_state_without_duplicate_trajectory() -> None:
    """快照状态必须 JSON 兼容，且轨迹只在专用字段保存一次。"""
    state = make_state()
    snapshot = RunSnapshotFactory().build(state)  # type: ignore[arg-type]

    assert "trajectory" not in snapshot.final_state
    assert snapshot.final_state["budget"]["max_steps"] == 5
    assert isinstance(snapshot.final_state["metadata"]["request_id"], str)
    assert snapshot.trajectory == []


def test_archive_rejects_duplicate_and_isolates_nested_mutation() -> None:
    """归档内的快照不能被保存方或读取方的嵌套修改污染。"""
    snapshot = RunSnapshotFactory().build(make_state())  # type: ignore[arg-type]
    archive = InMemoryRunArchive()
    archive.save(snapshot)

    snapshot.final_state["user_query"] = "已被外部修改"
    loaded = archive.load(snapshot.run_id)
    loaded.final_state["user_query"] = "已被读取方修改"

    assert archive.load(snapshot.run_id).final_state["user_query"] == "支付服务请求超时"
    with pytest.raises(ValueError, match="snapshot already exists"):
        archive.save(snapshot)
    with pytest.raises(KeyError, match="snapshot not found"):
        archive.load(uuid4())


class QueueActionProvider:
    """提供一次固定最终回答，避免测试依赖真实模型。"""

    def __init__(self, action: AgentAction) -> None:
        self._actions = deque([action])

    async def propose_action(self, state: dict[str, object]) -> AgentAction:
        """返回预设动作。"""
        del state
        return self._actions.popleft()


class UnusedToolExecutor:
    """最终回答路径不应调用工具。"""

    async def execute(self, action: AgentAction) -> dict[str, object]:
        """若被调用则说明路由错误。"""
        del action
        raise AssertionError("tool executor must not be called")


@pytest.mark.asyncio
async def test_harness_archives_terminal_state_with_checkpoint_event() -> None:
    """任何终止状态都应归档，且快照包含 checkpoint 事件。"""
    archive = InMemoryRunArchive()
    loop = HarnessLoop(
        action_provider=QueueActionProvider(
            AgentAction(
                action_type=ActionType.FINAL_ANSWER,
                intent="输出诊断结论",
                reason="测试快照归档。",
                report=diagnosis_report(),
            )
        ),
        tool_executor=UnusedToolExecutor(),
        policy=ActionPolicy([]),
        run_archive=archive,
    )

    result = await loop.run(make_state())  # type: ignore[arg-type]
    snapshot = archive.load(UUID(result["run_id"]))

    assert result["terminal_status"] is HarnessStatus.BLOCKED
    assert snapshot.terminal_status is HarnessStatus.BLOCKED
    assert snapshot.trajectory[-1].event_type is EventType.CHECKPOINT_SAVED
    assert "trajectory" not in snapshot.final_state
