# 第 25 阶段：离线 Benchmark 框架

## 目标

将单份运行轨迹评测扩展为可批量执行的离线 benchmark。每个样本除复用轨迹质量检查外，还可声明终止状态、根因关键词和必需证据工具。

## 执行规则

- `BenchmarkSubject` 负责将评测样本运行成 `RunSnapshot`。
- `OfflineBenchmarkRunner` 顺序执行样本，避免工具、预算或成本数据相互干扰。
- 单样本分数由轨迹检查和样本业务检查共同计算。
- 批量分数为全部样本分数的平均值；只有全部样本通过时，汇总结果才通过。
- 空样本集会被拒绝，不产生无统计意义的分数。

## 范围限制

本阶段定义通用 benchmark 接口，不绑定真实模型、LangSmith 或数据库。后续可让真实 Harness、固定场景执行器或 LangSmith 数据集适配 `BenchmarkSubject`。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/benchmark.py` | 顺序执行样本并汇总轨迹与业务期望检查。 |
| `tests/test_offline_benchmark.py` | 覆盖批量通过、业务期望失败和空样本集。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
