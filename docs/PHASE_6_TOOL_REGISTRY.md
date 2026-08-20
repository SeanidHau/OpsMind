# 第 6 阶段：工具注册表与受控执行

## 目标

本阶段引入 `ToolRegistry`，把工具描述、风险等级、参数约束和异步处理函数注册为统一实体。Harness Loop 继续通过 `ActionPolicy` 决定是否执行；`ToolRegistry` 只负责执行已经获准的 `call_tool` 动作。

本阶段不实现真实日志、指标、拓扑或工单数据。处理函数使用可替换的模拟实现，后续阶段再接入场景数据与外部服务。

## 工具定义

`ToolDefinition` 包含：

- `name`：稳定工具名称；同名注册在启动时失败。
- `description`：供模型适配器和文档使用的工具说明。
- `risk_level`：投影为 `ToolPolicy`，保持执行策略唯一来源。
- `required_args`：调用前必须提供的参数。
- `allowed_args`：允许传入的全部参数；未声明字段在处理函数执行前被拒绝。

## 执行边界

```text
AgentAction → ActionPolicy → ToolRegistry → async handler → observation
```

`ToolRegistry` 不消费预算、不决定审批，也不捕获并隐藏处理函数异常。这些运行时控制分别由 Harness、Policy 和后续重试层负责。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

验收覆盖工具执行、策略投影、缺失参数、未知参数、重复注册，以及注册表经 Harness Loop 的端到端执行。
