"""Streamlit 工作台的 SSE 协议解析测试。"""

import pytest

from frontend.client import BackendProtocolError, parse_sse_lines


def test_parse_sse_lines_preserves_event_order_and_payload() -> None:
    """工作台必须按后端顺序解析多条 JSON SSE 事件。"""
    events = list(
        parse_sse_lines(
            [
                "event: run_started",
                'data: {"run_id":"run-1"}',
                "",
                "event: tool_finished",
                'data: {"event_type":"tool_finished","tool_name":"query_metrics"}',
                "",
            ]
        )
    )

    assert [(event.event, event.data) for event in events] == [
        ("run_started", {"run_id": "run-1"}),
        (
            "tool_finished",
            {"event_type": "tool_finished", "tool_name": "query_metrics"},
        ),
    ]


def test_parse_sse_lines_rejects_non_json_payload() -> None:
    """协议错误不能被悄悄当成空运行。"""
    with pytest.raises(BackendProtocolError, match="valid JSON"):
        list(parse_sse_lines(["event: run_started", "data: not-json", ""]))
