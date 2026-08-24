# 第 36 阶段：调用延迟观测

## 目标

填充事件契约中的 `latency_ms` 字段，记录模型调用和工具执行的真实耗时，为运行回放、离线评测和后续时间线接口提供性能数据。

## 计时规则

- 使用单调时钟测量耗时，不依赖系统时间调整。
- 模型延迟只覆盖一次真实 `ActionProvider.propose_action` 调用，不包含预算检查、重试退避和后续动作校验。
- 工具延迟只覆盖一次真实 `ToolExecutor.execute` 调用，不包含策略检查、证据提取和进度验证。
- 延迟按毫秒向上取整，确保实际调用不会被记录为 `0` ms。

## 事件写入

- 每次模型调用在 `MODEL_CALLED` 事件上写入 `latency_ms`。
- 工具成功时在 `TOOL_FINISHED` 事件上写入 `latency_ms`。
- 工具失败时，在 `TOOL_RETRY`、`ACTION_BLOCKED` 或 `RUN_FAILED` 事件上写入本次尝试的 `latency_ms`。
- 事件仍保留原有的错误分类、预算和轨迹顺序。

## 边界

- 本阶段记录调用耗时，不统计供应商 Token、成本或队列等待时间。
- 运行总时限仍由第 32 阶段的运行时预算控制；延迟观测不会改变路由决策。
- 延迟数据随运行快照归档，可由现有 replay 和 trajectory evaluation 读取。

## 验收

`tests/test_harness_latency.py` 使用带异步延迟的模型提供器和工具执行器，验证成功模型调用与工具执行事件都包含非负整数毫秒耗时。
