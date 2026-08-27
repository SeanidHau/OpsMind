# 第 50 阶段：内置故障场景目录

## 目标

为 MVP 提供可复现的固定故障场景，并让应用工厂在未显式注入场景存储时加载该目录。

## 内置场景

| 场景 ID | 服务 | 故障类型 |
| --- | --- | --- |
| `order_http_5xx_001` | `order-service` | HTTP 5xx 错误率升高 |
| `payment_connection_pool_001` | `payment-service` | 数据库连接池耗尽 |
| `inventory_latency_001` | `inventory-service` | 接口响应延迟升高 |
| `recommendation_redis_cache_001` | `recommendation-service` | Redis 缓存异常或命中率下降 |

每个场景包含固定日志、指标和依赖，用于后续 Harness、工具调用和离线评测。场景数据仅用于本地演示，不连接真实生产系统。

## 应用注入

`create_app()` 默认调用 `create_default_scenario_store()`。每次调用都会创建独立的 `ScenarioStore`，避免一个测试或应用实例修改场景后影响其他实例。

调用方仍可以通过 `create_app(scenario_store=...)` 注入自定义场景目录。`GET /api/v1/scenarios` 继续只返回脱敏摘要。
