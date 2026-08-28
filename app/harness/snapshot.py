"""运行快照的构建与进程内归档。"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.models.contracts import DiagnosisState, RunSnapshot


class RunArchive(Protocol):
    """保存和读取不可变运行快照的最小接口。"""

    async def save(self, snapshot: RunSnapshot) -> None:
        """保存一份新快照；同一运行只能保存一次。"""

    async def load(self, run_id: UUID) -> RunSnapshot:
        """按运行 ID 返回已保存的快照。"""

    async def replace(self, snapshot: RunSnapshot) -> None:
        """替换已存在运行的最新快照。"""


class InMemoryRunArchive:
    """用于本地开发和测试的进程内快照归档。"""

    def __init__(self) -> None:
        self._snapshots: dict[UUID, RunSnapshot] = {}

    async def save(self, snapshot: RunSnapshot) -> None:
        """保存深拷贝，避免调用方后续修改嵌套状态。"""
        if snapshot.run_id in self._snapshots:
            raise ValueError(f"snapshot already exists for run: {snapshot.run_id}")

        self._snapshots[snapshot.run_id] = snapshot.model_copy(deep=True)

    async def load(self, run_id: UUID) -> RunSnapshot:
        """读取深拷贝，避免归档内的快照被外部修改。"""
        try:
            return self._snapshots[run_id].model_copy(deep=True)
        except KeyError as error:
            raise KeyError(f"snapshot not found for run: {run_id}") from error

    async def replace(self, snapshot: RunSnapshot) -> None:
        """用深拷贝替换已有快照，拒绝不存在的运行。"""
        if snapshot.run_id not in self._snapshots:
            raise KeyError(f"snapshot not found for run: {snapshot.run_id}")

        self._snapshots[snapshot.run_id] = snapshot.model_copy(deep=True)


class PostgresRunArchive:
    """使用 PostgreSQL JSONB 保存完整运行快照。"""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        """在首次启动时创建最小归档表。"""
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS run_snapshots "
                    "(run_id UUID PRIMARY KEY, snapshot JSONB NOT NULL)"
                )
            )

    async def close(self) -> None:
        """释放数据库连接池。"""
        await self._engine.dispose()

    async def save(self, snapshot: RunSnapshot) -> None:
        """插入新快照，重复运行 ID 保持与内存归档一致的语义。"""
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO run_snapshots (run_id, snapshot) VALUES (:run_id, :snapshot)"
                    ),
                    {"run_id": snapshot.run_id, "snapshot": snapshot.model_dump(mode="json")},
                )
        except IntegrityError as error:
            raise ValueError(f"snapshot already exists for run: {snapshot.run_id}") from error

    async def load(self, run_id: UUID) -> RunSnapshot:
        """读取快照，并交由契约模型校验历史数据。"""
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT snapshot FROM run_snapshots WHERE run_id = :run_id"),
                    {"run_id": run_id},
                )
            ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"snapshot not found for run: {run_id}")
        return RunSnapshot.model_validate(row)

    async def replace(self, snapshot: RunSnapshot) -> None:
        """替换已存在快照，避免把恢复调用意外变成新运行。"""
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text("UPDATE run_snapshots SET snapshot = :snapshot WHERE run_id = :run_id"),
                {"run_id": snapshot.run_id, "snapshot": snapshot.model_dump(mode="json")},
            )
        if result.rowcount != 1:
            raise KeyError(f"snapshot not found for run: {snapshot.run_id}")


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
