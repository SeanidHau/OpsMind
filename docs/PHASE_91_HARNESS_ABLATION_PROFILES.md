# Harness 组件消融配置

本阶段为端到端诊断基准提供可重复的 Harness 组件消融配置。所有配置复用同一个 `HarnessLoop`，不维护第二套 Agent 实现。

## 配置范围

| 配置 | Context Manager | Progress Verifier | 行为变化 |
| --- | --- | --- | --- |
| `full` | 启用 | 启用 | 使用完整 Harness 路径。 |
| `without_context_manager` | 关闭 | 启用 | 动作提供器只接收原始用户任务；系统仍记录上下文构建事件。 |
| `without_progress_verifier` | 启用 | 关闭 | 工具成功后直接重建上下文；完成运行不生成进展评估。 |

`without_context_manager` 不删除 `model_context` 字段。Harness 为动作提供器保留只含 `user_query` 的最小、契约兼容上下文，避免把组件消融变成接口变化。

## 保持不变的边界

三种配置均保留以下约束：

- 同一组基准样本、模型配置和工具注册表；
- 预算检查、工具策略、审批、证据门禁和报告渲染；
- 运行事件、轨迹和归档；
- 基准结果的完成率、轨迹通过率、工具调用、模型调用、Token 和上下文长度指标。

因此，输出只适合评估两个 Harness 组件对当前诊断任务的影响。它不表示与没有预算、策略或审计能力的普通 Agent 进行了公平比较。

## 运行方法

先配置模型供应商。使用完全相同的环境和样本文件，分别执行：

```bash
uv run python -m scripts.run_benchmark --profile full
uv run python -m scripts.run_benchmark --profile without_context_manager
uv run python -m scripts.run_benchmark --profile without_progress_verifier
```

完整 50 条样本的运行方式如下：

```bash
uv run python -m scripts.run_benchmark \
  --cases-file data/evaluations/diagnosis_cases_full.json \
  --profile full \
  --fail-on-failure
```

每次输出的 JSON 顶层包含 `profile`。比较结果时，按 `profile` 分组读取 `metrics`，并保留对应的样本级结果和轨迹用于定位差异。
