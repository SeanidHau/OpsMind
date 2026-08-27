# 第 64 阶段：运行中 SSE API

本阶段提供 `POST /api/v1/runs/stream`。接口创建一次诊断运行，并以 Server-Sent Events（SSE）在同一 HTTP 请求中返回执行过程。

## 事件顺序

接口首先发送一次 `run_started`，其中包含新建的 `run_id`。Harness 在 LangGraph 节点提交事件后，按轨迹顺序发送对应的事件名和安全事件数据，例如 `model_called`、`tool_started`、`tool_finished`、`run_paused`、`run_completed`。

运行正常结束时，接口发送 `run_finished`。运行器发生未处理异常时，接口发送 `run_failed`。两个结束事件都表示当前 SSE 连接不再发送新事件。

## 数据与断连边界

实时事件与轨迹查询 API 使用同一安全投影，不包含工具参数、工具原始观察结果、模型输入摘要或完整动作内容。每个 SSE 请求使用独立事件队列和观察器，不会与其他运行串流。

客户端断开连接时，服务取消仍在运行的请求任务。归档完成后的事件回放仍可使用 `GET /api/v1/runs/{run_id}/events`。

应用未配置支持请求专属观察器的运行器时，接口返回 `503` 和 `streaming diagnosis runtime is not configured`。
