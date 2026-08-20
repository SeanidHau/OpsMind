# 第 8 阶段：RAG 文档加载与确定性切分

## 目标

本阶段实现 RAG Ingestion Pipeline 的前两步：读取 Markdown 文档，并切分为带稳定标识的知识分块。输出同时保留 Pydantic 契约和 LangChain `Document`，供后续 BM25、embedding 和 Qdrant 复用。

本阶段不生成 embedding、不写入 Qdrant，也不执行检索。

## 输入格式

Markdown 文件可选使用 YAML 风格 Front Matter：

```markdown
---
service: payment-service
document_type: runbook
severity: P1
---
# 支付服务超时排查
```

Front Matter 中的键值写入 `metadata`。第一个一级标题写入 `metadata.title`。Front Matter 不进入 `KnowledgeDocument.content`。

## 分块规则

- `chunk_size` 按字符计算，必须大于 0。
- `chunk_overlap` 必须大于等于 0 且小于 `chunk_size`。
- 分块 ID 由 `source_id`、分块索引和内容计算；相同输入重复运行得到相同 ID。
- 每个 LangChain `Document` 都包含 `source_id`、`chunk_id`、`chunk_index` 和原始 metadata。

## 新增模块

| 路径 | 职责 |
| --- | --- |
| `app/rag/documents.py` | 加载 Markdown、切分内容和导出 LangChain 文档。 |
| `app/models/contracts.py` | 定义 `KnowledgeDocument` 和 `KnowledgeChunk`。 |

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

验收覆盖 Front Matter、正文隔离、稳定分块、重叠窗口、LangChain 元数据和非法窗口配置。
