# 第 9 阶段：BM25 关键词检索与元数据过滤

## 目标

本阶段在第 8 阶段的 `KnowledgeChunk` 上实现内存 BM25 检索。检索结果使用稳定的 `RetrievalHit` 契约，包含来源分块、分数和排名。

本阶段不连接 Qdrant、不生成 embedding，也不执行 RRF 融合。

## 检索规则

- 英文、数字和下划线按连续词元切分。
- 中文按单个汉字切分，以支持不依赖额外分词器的确定性关键词匹配。
- `metadata_filter` 使用全等匹配，并在 BM25 评分前过滤候选分块。
- 只返回分数大于 0 的结果。
- 结果按分数降序排序；同分时按 `chunk_id` 升序排序。
- 返回结果重新编号为从 1 开始的 `rank`。

## 新增模块

| 路径 | 职责 |
| --- | --- |
| `app/rag/bm25.py` | 建立内存索引并执行关键词检索。 |
| `app/models/contracts.py` | 定义 `RetrievalHit`。 |

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

验收覆盖相关性排序、元数据过滤、同分稳定顺序和非法查询参数。
