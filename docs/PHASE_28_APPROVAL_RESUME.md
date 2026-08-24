# 第 28 阶段：获批动作续跑

## 目标

让 `WAITING_APPROVAL` 运行在人工批准后，从保存的 checkpoint 继续执行原工具动作。续跑不重新请求模型来提议同一动作。

## 前置条件

- 运行已保存为 `WAITING_APPROVAL` checkpoint。
- checkpoint 包含 `current_action`、`approval_request` 和原始预算状态。
- 审批命令为 `ApprovalDecision.APPROVE`。

## 流程

1. `resolve_approval()` 恢复等待审批的 checkpoint，记录 `RUN_RESUMED`，并替换该运行的最新快照。
2. `resume_approved()` 再次恢复快照。只有未终止且已批准的运行可以续跑。
3. LangGraph 从 `START` 路由到 `approve_action`，直接使用 checkpoint 中的 `current_action`。
4. `approve_action` 重新执行工具注册和预算检查。审批只跳过重复的人审等待，不绕过策略约束。
5. 策略允许时，Loop 消费动作预算并执行工具；工具完成后继续原有的进度校验和最终报告流程。
6. 每次决议或续跑结束后，Loop 追加 `CHECKPOINT_SAVED` 并替换该运行的最新快照。

## 阻断规则

- 拒绝审批后，运行进入 `BLOCKED`，不能调用 `resume_approved()`。
- 获批后，如果工具不再注册或预算已耗尽，`approve_action` 写入 `ACTION_BLOCKED` 并结束运行。
- 获批动作必须与 `ApprovalResolution.action` 完全一致；不一致时视为状态损坏并抛出错误。

## 快照语义

`RunArchive.save()` 只创建新运行的首个快照。`RunArchive.replace()` 只替换已经存在的运行快照，并拒绝未知运行 ID。两个操作都保存深拷贝，避免调用方修改归档状态。

## 验收覆盖

- 批准后直接执行原高风险工具，且不重新提议该工具动作。
- 拒绝后的 checkpoint 不能续跑。
- 审批后预算发生变化时，续跑仍然会被预算策略阻断。
- 快照替换仅作用于已有运行，且保持深拷贝隔离。

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
