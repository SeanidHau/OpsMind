---
service: payment-service
document_type: runbook
incident_type: connection_pool_exhausted
---
# 支付服务数据库连接池耗尽排查

当支付请求超时，且数据库连接池使用率接近 100% 时，数据库连接池耗尽是可能原因。支付服务依赖 `postgres-primary`。

1. 查询 `payment-service` 的 `database_pool_utilization`、错误率和 P95 延迟。
2. 查询包含 `connection pool` 或 `timeout` 的支付服务日志。
3. 查询支付服务拓扑，确认 `postgres-primary` 是当前依赖。
4. 如果日志包含 `database connection pool exhausted`，并且连接池使用率为 1.0，记录两项证据后再给出候选根因。

处理建议：检查连接泄漏、慢查询和连接池上限。修改连接池配置或重启服务属于变更操作，必须经过人工审批。
