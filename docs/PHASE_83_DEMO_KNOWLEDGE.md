# 第 83 阶段：演示知识库与一键入库

## 目标

本阶段提供四份与内置模拟场景对应的 Runbook，并提供 `scripts.ingest_knowledge` 一键写入 Milvus。Runbook 仅用于本项目的模拟诊断演示，不包含真实生产环境的操作指令。

## 包含的 Runbook

| 文件 | 对应服务 | 故障类型 |
| --- | --- | --- |
| `order-http-5xx-runbook.md` | `order-service` | HTTP 5xx。 |
| `payment-connection-pool-runbook.md` | `payment-service` | 数据库连接池耗尽。 |
| `inventory-latency-runbook.md` | `inventory-service` | 查询延迟升高。 |
| `recommendation-redis-cache-runbook.md` | `recommendation-service` | Redis 缓存异常。 |

## 入库步骤

1. 启动 Milvus。

   ```bash
   docker compose up -d milvus
   ```

2. 在 `.env` 中配置 `EMBEDDING_MODEL`、Embedding 密钥和与模型输出一致的 `EMBEDDING_VECTOR_SIZE`。

3. 运行入库脚本。

   ```bash
   uv run python -m scripts.ingest_knowledge
   ```

脚本默认读取 `KNOWLEDGE_SOURCE_DIRECTORY`，其默认值为 `data/knowledge/`。使用 `--source-dir` 可以指定其他 Markdown 目录。应用查询时也必须使用相同目录。目录必须存在且至少包含一个 `.md` 文件。

脚本按文件名顺序处理文档。稳定分块 ID 与 Milvus upsert 使重复运行保持幂等。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
