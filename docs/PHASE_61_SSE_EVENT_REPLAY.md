# 第 61 阶段：SSE 事件回放 API

本阶段提供 `GET /api/v1/runs/{run_id}/events`。接口使用 Server-Sent Events（SSE）按记录顺序回放已归档运行的安全轨迹。

## 前置条件与限制

调用前，运行必须已经归档。接口读取缓存快照，不重新调用模型、工具或 Harness 节点。接口当前不推送执行中的新事件；执行中推送需要后续的事件发布机制。

## 事件格式

每条轨迹事件使用 `trajectory_event` 事件名。事件数据与轨迹查询 API 的单条安全事件一致。流末尾发送一次 `stream_completed`，其中包含 `run_id`、`event_count` 和运行状态。

响应不包含工具参数、工具原始观察结果、模型输入摘要或完整动作内容。客户端可以使用 `EventSource` 订阅 `trajectory_event` 和 `stream_completed`，按收到顺序更新运行时间线。

## 错误处理

未知 `run_id` 在建立事件流前返回 `404` 和 `run not found`。应用未配置运行查询能力时，接口返回 `503` 和 `diagnosis run reader is not configured`。
