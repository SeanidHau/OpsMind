"""工具目录 API 的验收测试。"""

from fastapi.testclient import TestClient

from app.api.main import create_app


def test_tools_endpoint_returns_sorted_read_only_scenario_tool_policies() -> None:
    """工具目录只公开策略摘要，并按名称稳定排序。"""
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/tools")

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "query_logs",
            "risk_level": "low",
            "read_only": True,
            "requires_approval": False,
            "required_args": ["service"],
            "allowed_args": ["contains", "service"],
            "max_calls_per_run": None,
        },
        {
            "name": "query_metrics",
            "risk_level": "low",
            "read_only": True,
            "requires_approval": False,
            "required_args": ["service"],
            "allowed_args": ["service"],
            "max_calls_per_run": None,
        },
        {
            "name": "query_topology",
            "risk_level": "low",
            "read_only": True,
            "requires_approval": False,
            "required_args": ["service"],
            "allowed_args": ["service"],
            "max_calls_per_run": None,
        },
    ]
