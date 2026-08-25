# 第 40 阶段：单工具调用上限

## 目标

为单个工具增加独立的调用上限。全局工具预算限制整个运行的调用总量；单工具调用上限限制某个工具在一次运行中的真实执行次数，避免不稳定或低价值工具占用全部预算。

## 配置来源

`ToolDefinition.max_calls_per_run` 是工具级预算配置的唯一来源。`ToolRegistry.policies()` 将该字段投影到 `ToolPolicy`。

字段默认值为 `None`，表示不限制该工具的单独调用次数，保持既有行为。设置该字段时，值必须大于零。

## 计数规则

单工具调用上限统计 `TOOL_STARTED` 事件，因此以下调用都计入次数：

- 首次工具执行；
- 工具成功执行；
- 工具失败执行；
- 自动重试。

模型提出的重复动作会先经过第 39 阶段的重复调用检查。工具失败后的自动重试不经过完整 Policy，但会在下一次真实执行前检查单工具调用上限。

## 阻断结果

达到上限时，Harness 返回 `PolicyOutcome.BLOCK` 或在重试路径中结束运行：

- 原因：`该工具已达到本次运行的调用上限。`
- 违规标识：`tool:call_limit:{tool_name}`

被阻断的下一次调用不会增加全局工具预算。已经发生的失败调用仍会保留在轨迹和 `tool_call_count` 中。

## 验收覆盖

- `tests/test_action_policy.py` 验证单工具上限不依赖参数差异。
- `tests/test_tool_registry.py` 验证注册表投影单工具预算配置。
- `tests/test_harness_loop.py` 验证模型提出的第二次调用不会执行。
- `tests/test_harness_tool_retry.py` 验证自动重试不能绕过单工具上限。
