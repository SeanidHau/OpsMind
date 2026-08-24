# 第 32 阶段：运行时限预算

## 目标

为一次诊断任务及其审批后的续跑加入累计墙钟时间预算，避免单次图执行或多次续跑无限占用运行资源。

## 实现

- `HarnessLoop.run` 与 `HarnessLoop.resume_approved` 统一进入 `_run_and_archive`。
- 运行开始时根据 `BudgetState.remaining_runtime_seconds` 创建异步超时边界。
- 图通过 `astream(stream_mode="values")` 执行，并持续保存最近一个完整状态。
- 发生超时时，Harness 追加 `ACTION_BLOCKED` 事件，节点为 `runtime_budget`，将任务标记为 `BLOCKED`，并记录错误原因。
- 每次 `run` 或 `resume_approved` 结束后，以单调时钟计算本次消耗。结果按秒向上取整，且不超过当时剩余预算，再通过 `BudgetManager` 累计写回状态。
- 无论正常结束还是超时，最终状态都会先追加 checkpoint 事件，再归档为可恢复快照。

## 语义边界

- 预算只统计 Harness 实际执行图的墙钟时间；等待人工审批的间隔不计入运行时长。
- 预算状态会被归档，因此审批后的续跑会继承并消耗剩余时长，不会重置预算。
- 本阶段不提供单个工具、模型调用的独立耗时分摊，也不处理跨进程的外部截止时间。

## 验收

`tests/test_harness_runtime_budget.py` 覆盖以下场景：

1. 运行时预算已耗尽时，任务被阻断并保存快照。
2. 正常结束的图至少消费一秒运行时长，符合按秒向上取整规则。
3. 审批前运行与审批后续跑的运行时长会累计到同一份预算中。
