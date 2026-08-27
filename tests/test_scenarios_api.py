"""场景目录 API 与场景存储隔离的验收测试。"""

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.models.contracts import IncidentScenario, ScenarioLog
from app.tools.scenarios import ScenarioStore


def make_scenario(
    *,
    scenario_id: str,
    service: str,
    metric_names: list[str],
) -> IncidentScenario:
    """构造用于目录接口的最小可复现场景。"""
    return IncidentScenario(
        scenario_id=scenario_id,
        service=service,
        logs=[
            ScenarioLog(
                timestamp="2026-08-27T10:00:00Z",
                level="ERROR",
                message="diagnostic evidence",
            )
        ],
        metrics={name: 1.0 for name in metric_names},
        dependencies=["postgres-primary"],
    )


def test_scenarios_endpoint_returns_an_empty_injected_catalog() -> None:
    """未注入场景数据时，目录接口返回空列表而不是伪造诊断证据。"""
    app = create_app(scenario_store=ScenarioStore(()))

    with TestClient(app) as client:
        response = client.get("/api/v1/scenarios")

    assert response.status_code == 200
    assert response.json() == []


def test_scenarios_endpoint_returns_sorted_sanitized_summaries() -> None:
    """目录接口按场景 ID 排序，且不返回日志正文和指标数值。"""
    store = ScenarioStore(
        [
            make_scenario(
                scenario_id="z-payment-timeout",
                service="payment-service",
                metric_names=["p95_latency_ms", "error_rate"],
            ),
            make_scenario(
                scenario_id="a-cache-miss",
                service="cache-service",
                metric_names=["hit_rate"],
            ),
        ]
    )

    with TestClient(create_app(scenario_store=store)) as client:
        response = client.get("/api/v1/scenarios")

    assert response.status_code == 200
    assert response.json() == [
        {
            "scenario_id": "a-cache-miss",
            "service": "cache-service",
            "log_count": 1,
            "metric_names": ["hit_rate"],
            "dependency_count": 1,
        },
        {
            "scenario_id": "z-payment-timeout",
            "service": "payment-service",
            "log_count": 1,
            "metric_names": ["error_rate", "p95_latency_ms"],
            "dependency_count": 1,
        },
    ]


def test_scenario_store_listing_returns_deep_copies() -> None:
    """目录读取方修改返回对象时，内部场景数据不得被污染。"""
    store = ScenarioStore(
        [
            make_scenario(
                scenario_id="payment-timeout",
                service="payment-service",
                metric_names=["error_rate"],
            )
        ]
    )

    listed = store.list_scenarios()
    listed[0].logs[0].message = "mutated by caller"

    assert store.get("payment-service").logs[0].message == "diagnostic evidence"
