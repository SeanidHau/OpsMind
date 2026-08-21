"""基于归档快照的无副作用 cached replay。"""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from app.harness.snapshot import RunArchive
from app.models.contracts import ReplayMode, ReplayResult


class CachedReplayService:
    """读取历史快照，不重新调用模型、工具或 LangGraph。"""

    def __init__(self, archive: RunArchive) -> None:
        """注入快照归档，便于后续替换为数据库实现。"""
        self._archive = archive

    def replay(self, run_id: UUID) -> ReplayResult:
        """返回独立副本，避免回放调用方修改归档内容。"""
        snapshot = self._archive.load(run_id)

        return ReplayResult(
            source_run_id=snapshot.run_id,
            mode=ReplayMode.CACHED,
            terminal_status=snapshot.terminal_status,
            final_state=deepcopy(snapshot.final_state),
            trajectory=[event.model_copy(deep=True) for event in snapshot.trajectory],
        )
