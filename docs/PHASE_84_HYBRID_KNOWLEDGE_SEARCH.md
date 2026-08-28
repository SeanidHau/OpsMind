# 第 84 阶段：混合知识检索

## 目标

本阶段将 `query_knowledge` 升级为混合检索。应用从 `KNOWLEDGE_SOURCE_DIRECTORY` 读取 Markdown，在内存中建立 BM25 索引；Milvus 提供同一目录已入库分块的向量召回。两条结果使用已有 RRF 实现融合。

## 配置与前置条件

`KNOWLEDGE_SOURCE_DIRECTORY` 默认指向 `data/knowledge/`。应用启动和入库脚本必须使用同一个目录。目录不存在或不包含 Markdown 文件时，配置了 Embedding 的应用会启动失败，以避免关键词索引和 Milvus 文档集不一致。

先使用以下命令将该目录写入 Milvus：

```bash
uv run python -m scripts.ingest_knowledge
```

## 查询结果

`query_knowledge` 的每个命中包含 `retriever_names`：

- `bm25` 表示关键词路径命中。
- `vector` 表示 Milvus 向量路径命中。

同一分块被两条路径命中时，RRF 融合结果同时保留两个名称。Harness 将该结果按现有工具观察、证据和上下文机制处理。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
docker compose up -d milvus
```
