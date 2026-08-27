"""MVP 内置故障场景与默认场景存储。"""

from app.models.contracts import IncidentScenario, ScenarioLog
from app.tools.scenarios import ScenarioStore


def build_default_scenarios() -> tuple[IncidentScenario, ...]:
    """返回四类 MVP 故障的确定性场景数据。"""
    return (
        IncidentScenario(
            scenario_id="order_http_5xx_001",
            service="order-service",
            logs=[
                ScenarioLog(
                    timestamp="2026-08-27T09:00:00Z",
                    level="ERROR",
                    message="upstream checkout-service returned HTTP 502",
                ),
                ScenarioLog(
                    timestamp="2026-08-27T09:01:00Z",
                    level="WARN",
                    message="order creation failed after upstream response error",
                ),
            ],
            metrics={
                "http_5xx_rate": 0.18,
                "error_rate": 0.18,
                "p95_latency_ms": 1_200.0,
            },
            dependencies=["checkout-service", "postgres-primary"],
        ),
        IncidentScenario(
            scenario_id="payment_connection_pool_001",
            service="payment-service",
            logs=[
                ScenarioLog(
                    timestamp="2026-08-27T10:00:00Z",
                    level="ERROR",
                    message="database connection pool exhausted",
                ),
                ScenarioLog(
                    timestamp="2026-08-27T10:01:00Z",
                    level="WARN",
                    message="payment request latency increased",
                ),
            ],
            metrics={
                "database_pool_utilization": 1.0,
                "error_rate": 0.12,
                "p95_latency_ms": 2_500.0,
            },
            dependencies=["postgres-primary", "order-service"],
        ),
        IncidentScenario(
            scenario_id="inventory_latency_001",
            service="inventory-service",
            logs=[
                ScenarioLog(
                    timestamp="2026-08-27T11:00:00Z",
                    level="WARN",
                    message="inventory query exceeded latency threshold",
                ),
                ScenarioLog(
                    timestamp="2026-08-27T11:01:00Z",
                    level="INFO",
                    message="downstream catalog-service response time increased",
                ),
            ],
            metrics={
                "error_rate": 0.02,
                "p95_latency_ms": 3_100.0,
                "p99_latency_ms": 4_700.0,
            },
            dependencies=["catalog-service", "postgres-replica"],
        ),
        IncidentScenario(
            scenario_id="recommendation_redis_cache_001",
            service="recommendation-service",
            logs=[
                ScenarioLog(
                    timestamp="2026-08-27T12:00:00Z",
                    level="WARN",
                    message="redis cache hit rate dropped below threshold",
                ),
                ScenarioLog(
                    timestamp="2026-08-27T12:01:00Z",
                    level="ERROR",
                    message="redis command timeout while reading recommendation cache",
                ),
            ],
            metrics={
                "cache_hit_rate": 0.31,
                "error_rate": 0.08,
                "p95_latency_ms": 1_800.0,
            },
            dependencies=["redis-primary", "user-profile-service"],
        ),
    )


def create_default_scenario_store() -> ScenarioStore:
    """创建每个应用实例独立使用的默认场景存储。"""
    # 返回新 Store，避免测试或调用方修改一个应用实例后影响另一个实例。
    return ScenarioStore(build_default_scenarios())
