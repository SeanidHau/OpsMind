# 第 13 阶段：Milvus 向量存储适配器

## 目标

本阶段使用 Milvus 持久化 `VectorizedChunk`，并将查询结果还原为统一的 `RetrievalHit`。适配器负责集合初始化、幂等写入、JSON 元数据过滤和向量查询。

单元测试使用 Milvus Lite 的独立临时目录。Docker Compose 提供 Milvus standalone，供本地服务验收使用。

## 存储规则

- 构造适配器时指定非空 `collection_name` 和正整数 `vector_size`。
- 首次写入或查询前，创建固定 schema、强一致性集合、`COSINE` 向量索引和 `metadata` JSON 字段。
- `chunk_id` 是字符串主键。重复写入同一分块时，Milvus 覆盖旧实体，不产生重复记录。
- `metadata_filter` 转换为 Milvus JSON 精确匹配表达式。
- 查询仅返回分数大于 0 的结果，并从 1 开始重新编号 `rank`。

## 模块

| 路径 | 用途 |
| --- | --- |
| `app/rag/milvus_store.py` | Milvus 集合管理、向量写入、查询和实体映射。 |
| `tests/test_milvus_vector_store.py` | Milvus Lite 下的建集合、幂等写入、过滤与参数边界测试。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
docker compose up -d milvus
```
