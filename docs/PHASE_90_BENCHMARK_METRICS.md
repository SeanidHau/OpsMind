# 第 90 阶段：基准实验指标

## 目标

本阶段为端到端基准的 JSON 输出增加统一指标。指标从每条已归档运行快照和轨迹事件计算，不调用额外模型或服务。

## 指标

| 字段 | 含义 |
| --- | --- |
| `completion_rate` | 终止状态为 `completed` 的样本比例 |
| `trajectory_pass_rate` | 通过轨迹完整性检查的样本比例 |
| `average_tool_calls` | 每条样本记录的平均工具调用数 |
| `duplicate_tool_call_rate` | `TOOL_FINISHED` 事件中，相同工具和参数的重复调用比例 |
| `average_model_calls` | 每条样本预算中记录的平均模型调用数 |
| `average_used_tokens` | 每条样本预算中记录的平均已用 Token 数 |
| `average_context_chars` | 每次 `CONTEXT_BUILT` 事件的平均上下文字符数 |
| `terminal_status_counts` | 各终止状态的样本数量 |

缺失或无法解析的历史预算数值按 `0` 计入指标。这个规则保证旧快照仍可比较，但不会把缺失值解释为真实消耗。

## 使用方法

运行现有基准命令即可获得 `metrics` 字段：

```bash
uv run python -m scripts.run_benchmark
```

后续实验组必须使用同一组样本文件和相同指标定义。比较模型版本或 Harness 配置时，应同时记录模型、提供商、样本文件和执行时间。
