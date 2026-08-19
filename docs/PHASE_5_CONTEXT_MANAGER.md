# 第 5 阶段：Context Manager

## 目标

本阶段在模型提出动作前构建有限、去重且可追溯的上下文快照。Harness 保留完整运行状态，但动作提供器只接收当前任务、计划、错误、证据和工具观察的受限视图。

本阶段不执行 LLM Token 精确计数、语义摘要或外部存储。字符上限是确定性保护措施；语义压缩在接入模型后实现。

## 上下文优先级

| 优先级 | 来源 | 说明 |
| --- | --- | --- |
| 100 | `task` | 当前用户任务，始终优先保留。 |
| 90 | `plan` | 当前诊断计划项。 |
| 80 | `error` | 已发生的执行错误。 |
| 70 | `evidence` | 结构化诊断证据。 |
| 60 | `tool_result` | 已执行工具的观察结果。 |

候选条目先按优先级选择，再按 `max_items` 和 `max_chars` 截断。相同来源、引用和内容的条目只保留一份。`ContextSnapshot.truncated` 用于明确告知下游上下文已被裁剪。

## 图集成

```text
START → build_context → propose_action → policy_check
                                      ↑
                         verify_progress
```

`build_context` 写入 `model_context`、`context_refs` 和 `context_built` 事件。`ActionProvider` 从 `DiagnosisState` 读取 `model_context`；它不能自行读取完整的原始工具结果。

## 新增契约与模块

| 路径 | 职责 |
| --- | --- |
| `app/models/contracts.py` | 定义 `ContextSource`、`ContextItem` 和 `ContextSnapshot`。 |
| `app/harness/context.py` | 构建并限制模型可见上下文。 |
| `app/harness/loop.py` | 插入 `build_context` 图节点。 |

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

验收覆盖任务保留、工具结果去重、预算截断、无效限制拒绝，以及图节点在动作提供器之前构建上下文。
