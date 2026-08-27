# 第 68 阶段：GPUI API 客户端

本阶段新增 `frontend/src/api_client.rs`。客户端访问 FastAPI 的 `GET /api/v1/health` 和 `GET /api/v1/scenarios`，为后续 GPUI 状态更新提供独立的网络层。

客户端要求健康检查响应同时满足 `status="ok"` 和 `service="opsmind"`。版本号为空、服务标识错误、字段缺失或出现未声明字段时，客户端返回错误，不把目标服务标记为可用。

场景目录只反序列化 `scenario_id`、`service`、日志数量、指标名称和依赖数量。客户端不会请求或保存原始日志、指标值及完整依赖详情。单次响应体上限为 512 KiB，网络请求超时为 5 秒。

本阶段包含健康检查、场景目录和错误服务标识的 Rust 单元测试。下一阶段会在 GPUI 事件循环中异步调用客户端，并将连接状态和场景目录渲染到控制台。
