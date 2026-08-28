# 第 80 阶段：RAG 文档入库

## 目标

本阶段将现有 Markdown 加载、确定性分块、Embedding 和 Milvus 写入串成一条同步入库链路。`KnowledgeIngestor` 不读取应用配置，也不建立网络连接。调用方负责创建 Embedding 客户端和 `MilvusVectorStore`。

## 入库流程

1. 调用 `ingest_markdown(path)` 读取一个 Markdown 文件，或调用 `ingest_document(document)` 传入 `KnowledgeDocument`。
2. 使用 `MarkdownChunker` 生成稳定 `KnowledgeChunk`。
3. 调用 `embed_documents`，并要求返回向量数量与分块数量完全一致。
4. 生成 `VectorizedChunk`，再通过 `upsert` 一次写入向量存储。

Embedding 数量不匹配时，入库停止，且不会执行写入。向量维度继续由 `MilvusVectorStore` 校验。

## 接入方式

`KnowledgeIngestor` 只要求 Embedding 客户端提供 `embed_documents(texts)`。因此可以传入 LangChain 的 `OpenAIEmbeddings`，也可以传入任何兼容 OpenAI Embeddings API 的客户端。客户端地址、密钥和模型由调用方配置。

```python
from langchain_openai import OpenAIEmbeddings
from pymilvus import MilvusClient

from app.rag.ingestion import KnowledgeIngestor
from app.rag.milvus_store import MilvusVectorStore

embedder = OpenAIEmbeddings(model="text-embedding-3-small")
store = MilvusVectorStore(
    client=MilvusClient(uri="http://127.0.0.1:19530"),
    collection_name="opsmind_knowledge",
    vector_size=1536,
)
KnowledgeIngestor(embedder=embedder, vector_store=store).ingest_markdown(path)
```

示例中的 `vector_size` 必须与 Embedding 模型输出维度一致。

## 模块与验收

| 路径 | 用途 |
| --- | --- |
| `app/rag/ingestion.py` | 文档加载、切分、向量化和存储写入编排。 |
| `tests/test_knowledge_ingestion.py` | 分块与向量对齐，以及写入前失败边界。 |

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
