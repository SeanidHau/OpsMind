# 第 82 阶段：动态工具目录

## 目标

本阶段在模型决策请求中加入 `available_tools`。目录由应用当前 `ToolRegistry` 中已注册的 `ToolDefinition` 生成，因此模型只会看到当前运行实际可调用的工具。

## 目录内容

每项工具仅包含以下字段：

- `name`
- `description`
- `required_args`
- `allowed_args`

目录不包含工具处理函数、基础设施连接信息、内部 Policy 状态或运行时对象。

## 运行规则

应用完成场景工具和可选 `query_knowledge` 的注册后，再创建模型动作提供器。动作提供器将目录和模型上下文一起发送给结构化输出模型。

模型选择 `call_tool` 时，只能使用 `available_tools` 中的名称和参数。Harness 仍会执行既有的参数校验、Policy、预算控制和证据处理；工具目录不授予额外权限。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
