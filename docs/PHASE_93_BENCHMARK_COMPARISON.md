# Harness 实验结果比较

本阶段提供离线结果比较工具。工具只读取 `scripts.run_benchmark` 已生成的 JSON 文件，不执行模型、工具或 RAG 查询。

## 生成结果文件

使用相同模型、样本文件和环境配置，分别运行需要比较的 Harness 配置：

```bash
mkdir -p data/runtime/benchmarks
uv run python -m scripts.run_benchmark --profile full \
  > data/runtime/benchmarks/full.json
uv run python -m scripts.run_benchmark --profile without_context_manager \
  > data/runtime/benchmarks/without_context_manager.json
uv run python -m scripts.run_benchmark --profile without_progress_verifier \
  > data/runtime/benchmarks/without_progress_verifier.json
```

完整评测集需要显式指定 `--cases-file data/evaluations/diagnosis_cases_full.json`。所有待比较的命令必须使用同一个 `--cases-file`。

## 输出比较报告

```bash
uv run python -m scripts.compare_benchmark_results \
  data/runtime/benchmarks/full.json \
  data/runtime/benchmarks/without_context_manager.json \
  data/runtime/benchmarks/without_progress_verifier.json
```

报告以 `full` 为基准。每个配置包含原始 `metrics` 与 `deltas_from_full`。差异的计算方向为「当前配置减去 `full`」：负的完成率、轨迹通过率或分数表示比完整 Harness 更低；负的模型调用或 Token 表示资源消耗更低。

## 可比性检查

比较命令要求存在且仅存在一份 `full` 结果。每份结果中的样本 ID 和顺序必须与 `full` 一致；不一致时命令停止并报错，不输出误导性的结论。

该检查不能保证模型服务完全确定。模型版本、提示词、工具数据和外部基础设施变化仍会影响实验结果，应与结果文件一起记录。
