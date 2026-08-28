# 第 86 阶段：RAG 离线检索评测

## 目标

本阶段为已经入库的知识库提供可重复的离线检索评测。评测样本固定保存在 `data/evaluations/retrieval_cases.json`，每条样本声明查询、预期 `source_id` 和可选服务过滤条件。

评测不调用聊天模型，也不修改 Milvus 数据；因此结果只反映当前知识文档、Embedding、向量集合和 BM25 索引的检索效果。

## 指标

- `Recall@K`：预期来源是否出现在前 K 条结果中。全部样本的命中比例即为该指标。
- `MRR`：每条样本取预期来源首次出现排名的倒数；未命中计为 `0`，再计算全部样本均值。

输出同时保留每条样本实际返回的 `source_id` 列表和首次命中排名，便于复核指标。

## 使用方法

先按 [README](../README.md) 配置 Embedding 并导入知识库：

```bash
uv run python -m scripts.ingest_knowledge
uv run python -m scripts.evaluate_retrieval
```

可选参数：

```bash
uv run python -m scripts.evaluate_retrieval --top-k 5
uv run python -m scripts.evaluate_retrieval --cases-file data/evaluations/retrieval_cases.json
```

命令输出 JSON，可被 CI 或后续评测看板直接读取。`passed` 仅表示当前全部固定样本均在前 K 条中命中，不替代人工对知识内容和诊断结论的审阅。
