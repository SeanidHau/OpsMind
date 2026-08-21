# 第 13 阶段：Qdrant 向量存储适配器

## 目标

本阶段使用 Qdrant 持久化 `VectorizedChunk`，并将查询结果还原为统一的 `RetrievalHit`。适配器负责集合初始化、幂等写入、元数据过滤、向量查询和 payload 还原。

单元测试使用 `QdrantClient(":memory:")`，不依赖 Docker 中的 Qdrant 服务。部署环境可使用相同的 Qdrant client 连接远程服务。

## 存储规则

- 构造适配器时指定非空 `collection_name` 和正整数 `vector_size`。
- 首次写入或查询前，如果集合不存在，则以 `COSINE` 距离创建集合。
- 点 ID 使用 `chunk_id` 生成确定性 UUID；重复写入同一分块会覆盖旧点，不产生重复记录。
- payload 保存 `chunk_id`、`source_id`、`index`、`content` 与完整 `metadata`。
- `metadata_filter` 映射为 Qdrant 的嵌套 `metadata.<key>` 精确匹配条件。
- 查询仅返回分数大于 0 的结果，并从 1 开始重新编号 `rank`。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/rag/qdrant_store.py` | Qdrant 集合管理、向量写入、查询和 payload 映射。 |
| `tests/test_qdrant_vector_store.py` | 内存 Qdrant 下的建集合、幂等写入、过滤与参数边界测试。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
