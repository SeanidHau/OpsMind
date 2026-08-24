# 第 26 阶段：Checkpoint State Restore

## 目标

将归档快照中的 JSON 化最终状态恢复为可供 Harness 节点使用的 `DiagnosisState`。该能力为审批恢复、故障恢复和从 checkpoint 继续执行提供类型安全的输入。

## 恢复规则

- 快照元信息覆盖 `final_state` 中的同名字段。
- 恢复器深拷贝 `final_state` 和轨迹，避免恢复调用方污染归档。
- 预算、计划、证据、当前动作、策略决策、进度评估、模型上下文和诊断报告会重建为对应 Pydantic 对象。
- 缺少必填状态或状态无法通过契约校验时，恢复器抛出 `ValueError`。
- `restore_checkpoint()` 只读取归档和恢复类型，不调用模型、工具或 LangGraph 节点。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/restore.py` | 从 `RunSnapshot` 重建强类型 `DiagnosisState`。 |
| `tests/test_checkpoint_restore.py` | 覆盖类型重建、数据隔离、无效快照和无图执行恢复。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
