# 第 12 阶段：混合检索组合层

## 目标

本阶段将 BM25、向量检索和 RRF 融合封装为单一 `HybridRetriever`。调用方一次传入文本查询、查询向量、元数据过滤条件和 `top_k`，即可获得包含融合分数与检索器来源的证据列表。

本阶段不生成 embedding、不连接 Milvus，也不在 Harness Loop 中调用检索器。

## 执行顺序

1. 对文本查询执行 BM25 检索。
2. 对查询向量执行余弦相似度检索。
3. 将两条路径的命中以 `bm25` 和 `vector` 名称传给 RRF。
4. 返回按 RRF 分数稳定排序的 `FusedRetrievalHit`。

## 约束

- `top_k` 必须大于 0，并原样传递给两个检索器和 RRF。
- `metadata_filter` 必须同时传递给两个检索器。
- 两条路径都没有命中时，返回空列表。
- 该组合层不修改任一检索器的原始分数或排名。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/rag/hybrid.py` | 协调关键词检索、向量检索与 RRF 融合。 |
| `tests/test_hybrid_retriever.py` | 融合、过滤、空结果与参数边界测试。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
