# 第 33 阶段：模型调用重试

## 目标

当 Action Provider 出现临时异常时，Harness 在明确的次数和预算边界内重试模型调用。重试耗尽后，运行以可归档的失败状态结束，不让异常直接逃出图执行。

## 执行规则

- `HarnessLoop` 新增 `max_model_retries` 与 `model_retry_delay_seconds` 配置。两项配置均不得为负数。
- 每次实际模型请求先消费一次 `model_calls` 预算。首次请求和每次重试都属于独立的模型调用。
- 每次调用写入 `MODEL_CALLED` 事件。调用失败且仍可重试时，追加带错误信息的 `MODEL_RETRY` 事件。
- 重试等待使用指数退避。等待时间仍处于第 32 阶段定义的总运行时限内。
- 重试次数耗尽时，Harness 写入 `RUN_FAILED` 事件，状态为 `FAILED`，并归档最新状态。
- 下一次重试会超出模型调用预算时，Harness 写入 `ACTION_BLOCKED` 事件，状态为 `BLOCKED`，且不再调用模型。

## 边界

- 本阶段将 Action Provider 抛出的 `Exception` 视为可重试失败。取消信号不属于 `Exception`，因此仍由运行时限和调用方控制。
- 本阶段不按供应商错误码区分限流、网络故障和不可恢复错误；错误分类策略可在后续阶段单独引入。

## 验收

`tests/test_harness_model_retry.py` 覆盖以下场景：

1. 模型首次失败后重试成功，且实际调用次数计入模型预算。
2. 连续失败超过重试上限后，运行归档为 `FAILED`。
3. 重试前检测到模型预算耗尽时，运行归档为 `BLOCKED`。
4. 负数重试次数和负数退避时间在构造 Harness 时被拒绝。
