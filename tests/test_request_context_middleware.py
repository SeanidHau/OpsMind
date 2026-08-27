"""请求关联 ID 中间件的验收测试。"""

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.middleware import REQUEST_ID_HEADER


def test_health_endpoint_echoes_valid_request_id() -> None:
    """格式受控的客户端关联 ID 必须原样写回响应头。"""
    request_id = "diagnosis-run_20260827"

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: request_id})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == request_id


def test_health_endpoint_replaces_invalid_request_id() -> None:
    """非法关联 ID 不得进入日志上下文，必须替换为服务端 UUID。"""
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "invalid.id"})

    generated_request_id = response.headers[REQUEST_ID_HEADER]
    assert response.status_code == 200
    assert generated_request_id != "invalid.id"
    assert re.fullmatch(r"[0-9a-f]{32}", generated_request_id) is not None


def test_request_log_keeps_bound_context_fields(caplog: pytest.LogCaptureFixture) -> None:
    """结构化访问日志必须保留请求关联 ID、方法和路径。"""
    request_id = "trace_20260827"

    with caplog.at_level(logging.INFO):
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: request_id})

    completed_event = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if '"event": "http_request_completed"' in record.getMessage()
    )

    assert response.status_code == 200
    assert completed_event["request_id"] == request_id
    assert completed_event["http_method"] == "GET"
    assert completed_event["http_path"] == "/api/v1/health"
    assert completed_event["status_code"] == 200
    assert completed_event["latency_ms"] >= 0
