# 第 11 阶段：可复现的向量检索基线

## 目标

本阶段实现基于预计算向量和余弦相似度的内存检索器。它与 BM25 使用相同的 `RetrievalHit` 输出，因而可以直接作为 RRF 融合的输入。

本阶段不生成 embedding、不连接 Milvus，也不修改 Harness Loop。固定向量仅用于验证向量检索的排序、过滤和错误边界。

## 检索规则

- 每个 `VectorizedChunk` 包含一个 `KnowledgeChunk` 与非空向量。
- 初始化时所有向量必须具有相同维度，且不允许零向量。
- 查询向量必须与索引维度一致，且不允许零向量。
- `metadata_filter` 在余弦相似度计算前过滤候选分块。
- 只返回相似度大于 0 的结果。
- 结果按相似度降序排序；同分时按 `chunk_id` 升序排序。
- 返回结果重新编号为从 1 开始的 `rank`。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/rag/vector.py` | 内存向量索引、余弦相似度检索和元数据过滤。 |
| `tests/test_vector_retriever.py` | 排序、过滤、稳定性与输入边界测试。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
