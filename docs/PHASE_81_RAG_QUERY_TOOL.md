# 第 81 阶段：RAG 查询与 Harness 工具

## 目标

本阶段为 RAG 增加查询向量生成，并将 Milvus 检索注册为 Harness 的只读工具 `query_knowledge`。工具结果通过既有 `ToolRegistry`、Policy、EvidenceCollector 和 Context Manager 进入诊断运行轨迹。

## 配置

配置 `EMBEDDING_MODEL` 后，应用启动时创建 OpenAI Embeddings API 兼容客户端，并注册 `query_knowledge`。未配置 `EMBEDDING_MODEL` 时，应用不创建 Embedding 客户端，也不注册该工具。

Embedding 密钥按以下顺序读取：`EMBEDDING_API_KEY`、`OPENAI_API_KEY`、`LLM_API_KEY`。Base URL 按以下顺序读取：`EMBEDDING_BASE_URL`、`OPENAI_BASE_URL`、`LLM_BASE_URL`。因此可以使用 OpenAI 兼容网关。

`EMBEDDING_VECTOR_SIZE` 必须与 `EMBEDDING_MODEL` 的输出维度一致。`KNOWLEDGE_COLLECTION_NAME` 必须与入库时使用的 Milvus 集合名称一致。

Anthropic 配置继续用于聊天模型。当前 RAG Embedding 仅使用 OpenAI Embeddings API 兼容接口。

## 工具契约

| 字段 | 说明 |
| --- | --- |
| `query` | 必填。检索问题。空白查询会被拒绝。 |
| `service` | 可选。按 `metadata.service` 精确过滤。 |

工具最多返回 3 个命中，且每次运行最多调用 2 次。返回值包含 `chunk_id`、`source_id`、`content`、`metadata` 和 `score`，用于证据引用和后续上下文构建。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
docker compose up -d milvus
```
