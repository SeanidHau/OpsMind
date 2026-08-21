# 第 17 阶段：模型调用预算与路由拦截

## 目标

本阶段让 `BudgetState.max_model_calls` 在 Harness Loop 中真正生效。每次调用 `ActionProvider` 前，Harness 先检查并消费一个 `model_calls` 预算；预算不足时，运行在模型调用前结束。

本阶段不统计模型 token、耗时或成本。相关字段仍由后续模型适配器在获得供应商用量后填充。

## 执行规则

- `propose_action` 节点先检查 `BudgetConsumption(model_calls=1)`。
- 预算足够时，先写入新的 `BudgetState`，再调用 `ActionProvider`。
- 成功调用后依次写入 `MODEL_CALLED` 和 `ACTION_PROPOSED` 事件。
- 预算不足时，写入 `ACTION_BLOCKED` 事件、设置 `BLOCKED` 状态，并直接路由至 `finish`。
- 预算阻断时不调用 `ActionProvider`，也不写入 `current_action`。
- 模型调用预算与工具调用、步骤预算分别记录；同一模型动作之后仍由 Policy 决定工具动作是否可执行。

## 新增或调整模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/loop.py` | 在模型调用前消费预算，并增加预算阻断路由。 |
| `tests/test_harness_model_budget.py` | 覆盖正常消费、后续拦截和零预算边界。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
