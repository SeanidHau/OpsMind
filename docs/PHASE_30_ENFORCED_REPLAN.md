# 第 30 阶段：强制 Replan 协议

## 目标

将连续停滞后的重新规划从模型提示升级为 Harness 强制协议。Progress Verifier 请求重新规划后，模型必须先提交 `update_plan`，不能直接调用工具或输出最终回答。

## 状态与输入

Harness 使用以下状态字段记录一次重新规划：

- `replan_requested`：是否必须先提交新计划。
- `replan_reason`：触发重新规划的进度验证原因。
- `replan_feedback`：Harness 对上一条违规动作的拒绝说明。
- `replan_correction_count`：当前重新规划已拒绝的动作次数。

`LangChainActionProvider` 将这些字段传入模型。模型收到 `replan_feedback` 后，只能输出 `update_plan`。

## 路由规则

1. 连续停滞达到 Progress Verifier 的重新规划阈值时，`verify_progress` 写入 Replan 状态。
2. 下一次模型动作不是 `update_plan` 时，图路由到 `replan_correction`，不进入 Policy 或工具节点。
3. `replan_correction` 写入 `ACTION_BLOCKED`，并保留反馈供下一次模型调用使用。
4. 默认允许一次纠正机会。模型再次违反协议时，运行进入 `BLOCKED`。
5. 合法 `update_plan` 被 `apply_plan` 接受后，Harness 清理本轮 Replan 状态并继续诊断。

## 预算语义

被拒绝的模型动作已经消耗一次模型调用预算，但不消耗动作步骤或工具调用预算。这样可以限制无效输出，又不会把未执行的工具计入实际工具调用。

## 验收覆盖

- 连续停滞后拒绝直接工具调用，并接受下一次计划修订。
- 第二次忽略协议时停止运行，且违规工具不进入执行器。
- Replan 原因、反馈和纠正次数会传入模型输入。
- 负数纠正次数上限在 Harness 初始化时被拒绝。

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
