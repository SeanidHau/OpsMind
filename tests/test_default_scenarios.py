"""内置 MVP 场景与默认应用注入的验收测试。"""

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.scenarios.defaults import build_default_scenarios, create_default_scenario_store


def test_default_scenarios_cover_all_mvp_fault_categories() -> None:
    """默认目录必须提供四类 MVP 故障场景，并保持标识与服务唯一。"""
    scenarios = build_default_scenarios()

    assert [scenario.scenario_id for scenario in scenarios] == [
        "order_http_5xx_001",
        "payment_connection_pool_001",
        "inventory_latency_001",
        "recommendation_redis_cache_001",
    ]
    assert {scenario.service for scenario in scenarios} == {
        "order-service",
        "payment-service",
        "inventory-service",
        "recommendation-service",
    }
    assert all(
        scenario.logs and scenario.metrics and scenario.dependencies for scenario in scenarios
    )


def test_default_scenario_stores_are_independent() -> None:
    """不同应用实例的默认目录不得共享可变场景数据。"""
    first_store = create_default_scenario_store()
    second_store = create_default_scenario_store()

    first_store.get("payment-service").metrics["error_rate"] = 0.99

    assert second_store.get("payment-service").metrics["error_rate"] == 0.12


def test_application_exposes_default_scenario_summaries() -> None:
    """未注入场景目录时，API 必须返回内置场景的脱敏摘要。"""
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/scenarios")

    assert response.status_code == 200
    summaries = response.json()
    assert [item["scenario_id"] for item in summaries] == [
        "inventory_latency_001",
        "order_http_5xx_001",
        "payment_connection_pool_001",
        "recommendation_redis_cache_001",
    ]
    assert all("logs" not in item and "metrics" not in item for item in summaries)
