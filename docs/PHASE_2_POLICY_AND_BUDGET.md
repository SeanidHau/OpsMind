# 第 2 阶段：预算与动作策略

## 目标

本阶段在工具执行前增加两个独立组件：

- `BudgetManager`：判断预算是否可用，并在工具实际执行后返回新的预算状态。
- `ActionPolicy`：判断模型动作是否允许执行、是否需要审批，或是否必须阻止。

本阶段不调用模型、不执行工具，也不写入 LangGraph checkpoint。

## 文件边界

| 文件 | 职责 |
|---|---|
| `app/models/contracts.py` | 增加预算消费、工具策略和策略决策的数据类型。 |
| `app/harness/budget.py` | 实现预算检查与不可变消费。 |
| `app/harness/policy.py` | 根据动作、注册工具和预算生成策略决策。 |

## 新增数据契约

### `BudgetConsumption`

表示一次后续执行将消耗的资源。字段包括 `steps`、`tool_calls`、`model_calls`、`tokens`、`runtime_seconds` 和 `estimated_cost_usd`。所有字段默认值为零，且不能为负数。

### `ToolPolicy`

描述工具的注册信息：工具名称、风险等级、是否只读，以及中风险工具是否需要审批。

风险等级为 `low`、`medium` 和 `high`。高风险工具始终需要审批；中风险工具由 `requires_approval` 决定；低风险工具不需要审批。

### `PolicyDecision`

策略结果的 `outcome` 只能是：

- `allow`：可以进入工具执行阶段。
- `block`：不得执行。
- `require_approval`：保存状态并等待人工审批。

决策同时返回本次动作对应的 `BudgetConsumption`，以及阻止原因或超限维度。

## `BudgetManager` 规则

- `exceeded_dimensions` 返回全部超限维度，顺序固定为步骤、工具调用、模型调用、Token、运行时长和估算成本。
- `consume` 只在全部预算可用时返回新的 `BudgetState`。
- `consume` 不能修改传入的 `BudgetState`。
- 任一维度超限时，`consume` 抛出 `BudgetExceededError`，并在错误信息中列出超限维度。

## `ActionPolicy` 规则

1. 每个动作消耗一个执行步骤。
2. `call_tool` 额外消耗一次工具调用预算。
3. 未注册的工具返回 `block`，违规标识为 `tool:not_registered`。
4. 预算不足时返回 `block`，并返回全部超限维度。
5. 低风险工具返回 `allow`。
6. 高风险工具返回 `require_approval`。
7. 中风险工具仅在 `requires_approval=true` 时返回 `require_approval`。
8. `evaluate` 只生成决策，不能修改传入的 `BudgetState`。

## 验收

完成业务实现后，以下命令必须通过：

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```
