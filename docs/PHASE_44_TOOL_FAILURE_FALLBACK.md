# 第 44 阶段：工具失败受控降级

## 目标

让临时传输故障在自动重试耗尽后进入强制重新规划，而不是立即结束整个运行。模型可以基于失败信息选择替代工具、澄清追问或新的计划。

## 启用方式

`HarnessLoop.replan_on_tool_failure` 默认值为 `False`。启用后，仅 `fallback_eligible=True` 的失败允许降级。

默认分类器只将 `transient_transport_error` 标记为可降级。参数错误、权限错误和未分类错误仍按原有失败路径处理。

## 执行顺序

1. 工具发生临时传输故障。
2. Harness 在预算内执行自动重试。
3. 重试耗尽后，Harness 写入 `VERIFICATION_FAILED`，设置 `replan_requested`。
4. 下一轮模型调用必须先提交 `update_plan`。

失败工具不会被静默忽略。错误分类、原始错误和失败事件都会写入轨迹与上下文。
