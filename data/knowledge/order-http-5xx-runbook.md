---
service: order-service
document_type: runbook
incident_type: http_5xx
---
# 订单服务 HTTP 5xx 排查

当订单创建请求的 HTTP 5xx 错误率升高时，先确认 `checkout-service` 的响应状态和延迟。订单服务依赖 `checkout-service`，上游返回 502 时会导致订单创建失败。

1. 查询 `order-service` 的错误率和 P95 延迟。
2. 查询包含 `502` 或 `upstream` 的订单服务日志。
3. 查询订单服务拓扑，确认 `checkout-service` 是当前依赖。
4. 如果日志显示上游 502，记录上游错误作为候选根因证据。不要把订单服务自身的重试当作根因。

处理建议：先恢复 `checkout-service` 的可用性，再验证订单创建成功率是否恢复。
