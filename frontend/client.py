"""Streamlit 工作台使用的后端 HTTP 与 SSE 客户端。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx


class BackendProtocolError(RuntimeError):
    """后端响应不符合工作台预期的 SSE 协议。"""


@dataclass(frozen=True)
class ServerSentEvent:
    """一条已经解析的后端 SSE 事件。"""

    event: str
    data: dict[str, Any]


def parse_sse_lines(lines: Iterable[str]) -> Iterator[ServerSentEvent]:
    """将 SSE 文本行转换为结构化事件，拒绝非 JSON 数据负载。"""
    event_name = "message"
    data_lines: list[str] = []

    def build_event() -> ServerSentEvent | None:
        if not data_lines:
            return None
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as error:
            raise BackendProtocolError("SSE data must be valid JSON") from error
        if not isinstance(payload, dict):
            raise BackendProtocolError("SSE data must be a JSON object")
        return ServerSentEvent(event=event_name, data=payload)

    for line in lines:
        if not line:
            parsed_event = build_event()
            if parsed_event is not None:
                yield parsed_event
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())

    parsed_event = build_event()
    if parsed_event is not None:
        yield parsed_event


class OpsMindApiClient:
    """调用 OpsMind FastAPI 服务的轻量同步客户端。"""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def list_scenarios(self) -> list[dict[str, Any]]:
        """读取场景摘要，供工作台构造辅助诊断提示。"""
        try:
            response = httpx.get(f"{self._base_url}/api/v1/scenarios", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ConnectionError("无法连接 OpsMind API") from error

        payload = response.json()
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise BackendProtocolError("scenarios response must be a JSON array")
        return payload

    def stream_run(self, payload: dict[str, str]) -> Iterator[ServerSentEvent]:
        """创建运行并按后端发送顺序产出 SSE 事件。"""
        try:
            with httpx.stream(
                "POST",
                f"{self._base_url}/api/v1/runs/stream",
                json=payload,
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0),
            ) as response:
                response.raise_for_status()
                yield from parse_sse_lines(response.iter_lines())
        except httpx.HTTPError as error:
            raise ConnectionError("诊断运行连接已中断") from error
