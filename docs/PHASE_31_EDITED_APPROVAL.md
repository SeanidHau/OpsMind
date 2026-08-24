# 第 31 阶段：编辑后审批续跑

## 目标

在高风险工具等待人工审批时，允许审批人修改同一工具的参数。Harness 保存编辑后的动作，从 checkpoint 续跑，并在执行前重新校验工具策略和预算。

## 审批命令

`ApprovalDecision` 新增 `edit`。`ApprovalCommand.edited_action` 仅在 `edit` 时允许出现，并且必须是 `call_tool` 动作。

编辑动作必须保持与待审批动作相同的 `tool_name`。审批人可以修改 `tool_args`，但不能通过编辑命令替换为另一种工具。

## 续跑流程

1. `ApprovalResolver` 校验等待审批状态和编辑动作。
2. Resolver 将编辑后的动作写入 `current_action` 和 `ApprovalResolution.action`。
3. `resolve_approval()` 保存新的 checkpoint。
4. `resume_approved()` 接受 `approve` 或 `edit` 决议，并从 `approve_action` 进入执行路径。
5. `approve_action` 重新执行工具注册和预算检查；编辑审批不绕过现有安全约束。

## 阻断规则

- `edit` 没有 `edited_action` 时，命令契约拒绝请求。
- `edited_action` 不是 `call_tool` 时，命令契约拒绝请求。
- `approve` 或 `reject` 携带 `edited_action` 时，命令契约拒绝请求。
- 编辑后的 `tool_name` 与原待审批工具不一致时，Resolver 拒绝请求。

## 验收覆盖

- 同工具参数编辑会更新决议、当前动作和审计事件。
- 跨工具编辑被拒绝。
- 编辑后的 checkpoint 可恢复为强类型状态。
- 续跑只执行编辑后的动作，不重新提议原动作。

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
