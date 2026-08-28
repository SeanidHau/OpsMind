---
service: inventory-service
document_type: runbook
incident_type: high_latency
---
# 库存服务高延迟排查

当库存查询的 P95 或 P99 延迟升高时，先确认 `catalog-service` 的响应时间，再检查 `postgres-replica` 的查询状态。库存服务的下游依赖包括 `catalog-service` 和 `postgres-replica`。

1. 查询 `inventory-service` 的 P95 和 P99 延迟。
2. 查询包含 `latency`、`query` 或 `catalog` 的库存服务日志。
3. 查询库存服务拓扑，确认下游依赖。
4. 如果库存查询超时与目录服务响应时间升高同时出现，将目录服务延迟作为需要继续验证的证据，不要直接认定为根因。

处理建议：继续收集目录服务和数据库副本的状态，再判断故障位置。
