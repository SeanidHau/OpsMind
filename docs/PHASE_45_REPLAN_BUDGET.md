# 第 45 阶段：重规划预算

## 目标

限制一次运行中可完成的重新规划次数，避免模型在计划修订和工具调用之间反复循环。初始计划不计入重规划预算。

## 默认限制

`HarnessLoop.max_replans` 默认值为 `2`，允许传入 `0` 禁止任何重新规划。

Harness 在状态中保存 `replan_count`。模型上下文会收到该计数，但不会收到完整内部状态。

## 计数与阻断规则

以下计划提交计入一次重新规划：

1. 已存在初始计划后的 `update_plan`。
2. 因停滞触发且 `replan_requested=True` 的 `update_plan`。
3. 因工具临时故障降级触发且 `replan_requested=True` 的 `update_plan`。

当 `replan_count` 达到 `max_replans` 时，Harness 在停滞检测和工具降级入口直接阻断。模型主动提交额外 `update_plan` 时，Harness 也会在应用计划前阻断。

阻断结果写入 `ACTION_BLOCKED`、错误列表和 checkpoint。已生效的计划版本不会被覆盖。
