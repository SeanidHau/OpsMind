# 第 27 阶段：审批决议状态机

## 目标

将 `WAITING_APPROVAL` 状态转换为带审计信息的批准或拒绝决议。决议阶段只处理状态，不直接执行高风险工具。

## 规则

- 只有 `WAITING_APPROVAL` 且存在待审批请求的运行可以处理决议。
- 待审批动作必须是 `call_tool`。
- 批准后清除等待状态和审批请求，写入 `ApprovalResolution`，并记录 `RUN_RESUMED`。
- 拒绝后运行转为 `BLOCKED`，记录拒绝原因和 `ACTION_BLOCKED`。
- 决议会随 snapshot 恢复为强类型 `ApprovalResolution`。
- `resolve_approval()` 仅恢复 checkpoint 和处理决议，不调用模型、工具或 LangGraph 节点。

## 范围限制

本阶段不执行获批动作，也不保存审批后的新 checkpoint。下一阶段将把获批动作接入图路由和续跑流程。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/approval.py` | 校验并生成批准或拒绝的状态更新。 |
| `tests/test_approval_resolution.py` | 覆盖批准、拒绝、非法状态、快照恢复与无副作用决议。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
