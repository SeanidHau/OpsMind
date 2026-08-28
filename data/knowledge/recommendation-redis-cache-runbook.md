---
service: recommendation-service
document_type: runbook
incident_type: redis_cache
---
# 推荐服务 Redis 缓存异常排查

当 Redis 缓存命中率下降并出现读取超时时，先确认 `redis-primary` 的可用性。推荐服务还依赖 `user-profile-service`，因此需要区分缓存异常和下游用户信息服务异常。

1. 查询 `recommendation-service` 的 `cache_hit_rate`、错误率和 P95 延迟。
2. 查询包含 `redis`、`cache` 或 `timeout` 的推荐服务日志。
3. 查询推荐服务拓扑，确认 `redis-primary` 和 `user-profile-service`。
4. 如果日志包含 `redis command timeout`，且缓存命中率明显下降，将 Redis 缓存异常作为候选根因，并继续确认影响范围。

处理建议：先验证 Redis 连接与超时情况。清空缓存、切换实例或修改缓存策略属于变更操作，必须经过人工审批。
