"""运行快照的构建与进程内归档。"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.models.contracts import DiagnosisState, RunSnapshot


class RunArchive(Protocol):
    """保存和读取不可变运行快照的最小接口。"""

    def save(self, snapshot: RunSnapshot) -> None:
        """保存一份新快照；同一运行只能保存一次。"""

    def load(self, run_id: UUID) -> RunSnapshot:
        """按运行 ID 返回已保存的快照。"""


class InMemoryRunArchive:
    """用于本地开发和测试的进程内快照归档。"""

    def __init__(self) -> None:
        self._snapshots: dict[UUID, RunSnapshot] = {}

    def save(self, snapshot: RunSnapshot) -> None:
        """保存深拷贝，避免调用方后续修改嵌套状态。"""
        if snapshot.run_id in self._snapshots:
            raise ValueError(f"snapshot already exists for run: {snapshot.run_id}")

        self._snapshots[snapshot.run_id] = snapshot.model_copy(deep=True)

    def load(self, run_id: UUID) -> RunSnapshot:
        """读取深拷贝，避免归档内的快照被外部修改。"""
        try:
            return self._snapshots[run_id].model_copy(deep=True)
        except KeyError as error:
            raise KeyError(f"snapshot not found for run: {run_id}") from error


class RunSnapshotFactory:
    """将 LangGraph 状态转换为稳定、JSON 兼容的运行快照。"""

    def build(self, state: DiagnosisState) -> RunSnapshot:
        """提取最终状态和轨迹，排除重复保存的 trajectory 字段。"""
        final_state = {key: value for key, value in state.items() if key != "trajectory"}

        # 固定 JSON 编码后再解码，移除 UUID、datetime 与 Pydantic 对象等运行时类型。
        serialized_state = json.dumps(
            final_state,
            default=self._json_default,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        return RunSnapshot(
            run_id=UUID(state["run_id"]),
            session_id=state["session_id"],
            thread_id=state["thread_id"],
            terminal_status=state.get("terminal_status"),
            final_state=json.loads(serialized_state),
            trajectory=list(state["trajectory"]),
        )

    @staticmethod
    def _json_default(value: Any) -> Any:
        """转换 JSON 编码器不支持的状态值。"""
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, (UUID, datetime)):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        raise TypeError(f"unsupported snapshot value: {type(value).__name__}")
