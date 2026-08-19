# 第 1 阶段：Harness 核心契约

## 目标

本阶段定义 Harness 各模块之间交换的数据。完成后，计划、预算、模型动作、工具观察和执行轨迹使用统一类型；后续模块不得自行扩展同义字段。

业务实现文件为 `app/models/contracts.py`。测试文件为 `tests/test_contracts.py`。

## 类型边界

| 类型 | 用途 |
|---|---|
| `PlanItem` | 表示一个可执行的计划项及其状态。 |
| `BudgetState` | 记录各类上限、已使用量和剩余预算。 |
| `AgentAction` | 表示模型提出的下一步动作。 |
| `AgentEvent` | 记录模型、工具和状态变化产生的轨迹事件。 |
| `DiagnosisState` | LangGraph 节点之间传递的状态。 |

## 动作约束

- `call_tool` 必须提供非空的 `tool_name`。
- 除 `call_tool` 外的动作不得携带 `tool_name` 或非空 `tool_args`。
- 每个动作必须提供 `intent` 和 `reason`。
- 动作类型仅允许：`ask_user`、`call_tool`、`update_plan`、`final_answer`、`request_approval`、`fail`。

## 预算约束

预算包含步骤数、工具调用数、模型调用数、Token、运行时长和估算成本。

- 所有上限必须大于或等于零；步骤数、工具调用数、模型调用数和运行时长必须大于零。
- 已使用量不得为负数。
- 已使用量不得超过对应上限。
- `remaining_*` 属性返回上限减去已使用量。

## 轨迹约束

- `AgentEvent` 使用 UTC 时间。
- `run_id` 使用 UUID。
- `step_id` 从 0 开始，不能为负数。
- 事件中嵌套的动作和观察结果必须可序列化为 JSON。

## Graph State

`DiagnosisState` 使用 `TypedDict`，至少包含以下 Harness 字段：

- `plan`
- `budget`
- `trajectory`
- `progress_status`

其余诊断字段保留在 `docs/PROJECT_SPEC.md` 中定义的范围内。

## 验收标准

以下命令必须全部通过：

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```
