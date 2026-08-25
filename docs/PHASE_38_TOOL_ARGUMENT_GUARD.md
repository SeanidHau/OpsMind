# 第 38 阶段：工具参数前置校验

## 目标

将工具参数 schema 的校验从 `ToolRegistry.execute()` 前移到 `ActionPolicy.evaluate()`。Harness 在进入预算扣减、人工审批和工具处理函数之前，先拒绝缺失必填参数或携带未声明参数的工具动作。

## 参数 schema 的来源与兼容性

`ToolDefinition` 继续是工具参数 schema 的唯一来源。`ToolRegistry.policies()` 会将以下字段投影到 `ToolPolicy`：

- `required_args`：必须提供的参数名；
- `allowed_args`：允许提供的参数名。

`ToolPolicy.allowed_args` 使用 `None` 表示未声明参数 schema。这个值用于兼容已有的手工 `ToolPolicy`：策略层不会对这类策略执行参数校验。显式传入空元组 `()` 表示 schema 已声明，且工具不接受任何参数。

## 执行顺序

`call_tool` 动作按以下顺序经过 Harness：

1. 检查工具是否已注册。
2. 检查工具参数是否符合注册定义。
3. 检查动作预计消耗是否超出运行预算。
4. 检查工具风险策略是否要求人工审批。
5. 调用 `ToolRegistry.execute()`，由注册表保留执行前的防御性参数校验。

因此，参数不符合定义的动作不会调用处理函数，也不会增加 `used_tool_calls` 或 `tool_call_count`。

## 阻断结果

策略层返回 `PolicyOutcome.BLOCK`，阻断原因固定为 `工具参数不符合注册定义。`。`violations` 使用稳定、可检索的标识：

- 缺失参数：`tool:missing_arg:{name}`；
- 未声明参数：`tool:unexpected_arg:{name}`。

当一次动作同时存在多项问题时，缺失参数按名称排序在前，未声明参数按名称排序在后。

## 验收覆盖

- `tests/test_action_policy.py` 验证缺失参数在预算检查前阻断、未声明参数在审批前阻断，以及旧 `ToolPolicy` 的兼容行为。
- `tests/test_tool_registry.py` 验证注册表会投影参数 schema，并验证 Harness 对无效参数不调用工具、不消耗工具预算。
