# 第 29 阶段：显式计划与版本化修订

## 目标

将模型的诊断计划纳入 Harness 控制。模型使用 `update_plan` 提交完整计划。Harness 校验计划结构、保存计划历史，并通过 LangGraph 节点控制后续动作。

## 动作与状态

- `AgentAction.plan` 只允许出现在 `update_plan` 动作中，且必须包含 1 到 10 个计划项。
- `PlanRevision` 保存版本号、修订原因、完整计划和创建时间。
- `DiagnosisState.plan` 表示当前计划；`plan_history` 保留每一个已通过校验的历史版本。
- `PLAN_CREATED` 表示首次计划提交；`PLAN_REVISED` 表示后续计划修订。

## 校验规则

`PlanManager` 在写入状态前检查以下条件：

- 计划项 ID 不重复。
- 每个依赖项都属于当前提交的完整计划。
- 计划项不能依赖自身。
- 计划依赖图不能形成循环。

任一条件不满足时，Harness 写入 `ACTION_BLOCKED` 并将运行置为 `BLOCKED`。无效计划不会进入工具执行路径。

## 图路由

1. 模型提出 `update_plan`。
2. `policy_check` 消费该动作的步骤预算。
3. `apply_plan` 创建新的 `PlanRevision`，并记录计划事件。
4. Loop 重新构建最小上下文，再请求模型提出下一步动作。

模型提示会收到 `plan_version` 和 `replan_requested`。初始运行或 Progress Verifier 请求重新规划时，模型应先提交 `update_plan`。

## 兼容性

第 29 阶段之前保存的 checkpoint 不包含 `plan_history`。`RunStateRestorer` 将缺失字段恢复为空列表，旧快照仍可读取和续跑。

## 验收覆盖

- 合法计划的深拷贝、版本和依赖关系。
- 重复 ID、未知依赖、自依赖和循环依赖的阻断。
- 首次计划提交后的工具执行与最终报告。
- 后续计划修订的历史保留和轨迹事件。
- 模型提示中的计划版本与重新规划信号。

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
