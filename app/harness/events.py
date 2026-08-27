"""Harness 运行事件的可选观察接口。"""

from __future__ import annotations

from typing import Protocol

from app.models.contracts import AgentEvent


class HarnessEventObserver(Protocol):
    """接收单条已创建审计事件的同步观察器。"""

    def on_event(self, event: AgentEvent) -> None:
        """处理事件副本；实现不得修改 Harness 的执行流程。"""
