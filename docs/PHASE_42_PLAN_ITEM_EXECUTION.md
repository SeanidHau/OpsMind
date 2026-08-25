# 第 42 阶段：计划项绑定与执行状态

## 目标

将工具调用和澄清追问与显式计划项关联。Harness 在绑定动作执行前检查计划项存在性和依赖状态，并在工具成功后更新计划项状态。

## 动作绑定

`AgentAction.plan_item_id` 可绑定当前计划项。该字段仅允许出现在 `call_tool` 和 `ask_user` 动作中。

字段保持可选，以兼容已有调用方。LangChain 动作提供器会提示模型使用 Context Manager 暴露的 `plan:<UUID>` reference 填写该字段。

## 状态迁移

绑定计划项的状态迁移如下：

1. Harness 检查计划项存在，且所有依赖均为 `completed`。
2. Policy 放行后，计划项从 `pending` 变为 `in_progress`。
3. 绑定工具成功后，计划项从 `in_progress` 变为 `completed`。

策略阻断、审批等待和工具失败不会把计划项标记为 `completed`。状态更新使用不可变副本，避免节点修改共享 LangGraph 状态。

## 阻断结果

引用未知计划项或依赖未完成时，Harness 写入 `ACTION_BLOCKED` 并结束运行。常见原因包括：

- `action references an unknown plan item`
- `plan item dependencies are not completed`

这类阻断发生在预算消费前。

## 验收覆盖

- `tests/test_contracts.py` 验证 `plan_item_id` 的动作边界。
- `tests/test_plan_execution.py` 验证依赖检查、工具成功后的完成状态和越过依赖时的阻断。
