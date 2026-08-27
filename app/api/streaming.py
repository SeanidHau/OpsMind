"""运行中 SSE 使用的非阻塞事件队列观察器。"""

from __future__ import annotations

import asyncio

from app.models.contracts import AgentEvent


class QueueEventObserver:
    """将 Harness 事件放入当前请求独占的异步队列。"""

    def __init__(self, event_queue: asyncio.Queue[AgentEvent | None]) -> None:
        self._event_queue = event_queue

    def on_event(self, event: AgentEvent) -> None:
        """同步入队，避免观察器在 Harness 执行路径中等待网络 I/O。"""
        self._event_queue.put_nowait(event)
