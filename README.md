# OpsMind

OpsMind 是一个面向长流程任务的 Agent Harness 项目，使用运维故障诊断作为可复现 benchmark。

## 当前阶段

已完成受控 Harness、RAG 与模拟工具、用户输入续跑、高风险动作审批、运行回放、实时 SSE、GPUI 桌面工作台和可选 PostgreSQL 运行归档。

默认运行归档使用进程内存，服务重启后历史运行不可读取或回放。将 `RUN_ARCHIVE_BACKEND` 设为 `postgres` 后，应用在启动时创建 `run_snapshots` 表，并将快照写入 PostgreSQL。

## 技术栈

- Python 3.12
- LangChain、LangGraph、LangSmith
- FastAPI、Milvus、PostgreSQL（本地开发依赖）
- pytest、Ruff、mypy、Docker Compose

## 本地开发

### 前置条件

- 安装 `uv`。项目使用 `uv` 管理 Python 版本、虚拟环境和依赖锁定文件。
- 如需启动 PostgreSQL 和 Milvus，安装 Docker Desktop 并确保 Docker 服务正在运行。

### 初始化

1. 复制环境变量示例文件。

   ```bash
   cp .env.example .env
   ```

2. 创建 Python 3.12 虚拟环境并安装依赖。

   ```bash
   uv sync --all-groups
   ```

3. 可选：启动 PostgreSQL 和 Milvus。

   默认的模拟场景诊断不依赖这两个服务。设置 `RUN_ARCHIVE_BACKEND=postgres` 前，先启动 PostgreSQL。需要验证本地基础设施配置时，运行：

   ```bash
   docker compose up -d
   ```

### 保存运行记录到 PostgreSQL

在 `.env` 中设置：

```dotenv
RUN_ARCHIVE_BACKEND=postgres
```

启动 FastAPI 时，应用自动创建 `run_snapshots` 表。之后创建、等待输入、记录审批和续跑都会替换同一运行的最新快照；服务重启后可继续查询、回放或恢复该运行。

未启动 PostgreSQL 时，不要设置 `RUN_ARCHIVE_BACKEND=postgres`。应用启动会失败，以避免将配置错误静默降级为内存归档。

### 检查依赖就绪状态

`GET /api/v1/health` 只检查 FastAPI 进程。`GET /api/v1/ready` 额外检查启用的 PostgreSQL 归档和 Milvus。依赖未就绪时，`/ready` 返回 `503`，但不会返回连接串或原始异常。

```bash
curl http://127.0.0.1:8000/api/v1/ready
```

4. 运行项目验收。

   ```bash
   uv run pytest
   uv run ruff check .
   uv run mypy app
   docker compose config
   ```

### 配置模型供应商

在 `.env` 中至少配置一个支持的模型供应商。`LLM_PROVIDER` 仅支持 `openai` 或 `anthropic`。

OpenAI 兼容接口示例：

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=your-model-name
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://your-compatible-endpoint/v1
```

Anthropic 接口示例：

```dotenv
LLM_PROVIDER=anthropic
LLM_MODEL=your-model-name
ANTHROPIC_API_KEY=your-api-key
ANTHROPIC_BASE_URL=https://your-compatible-endpoint
```

供应商专用密钥优先于 `LLM_API_KEY`。未配置模型供应商时，健康检查和场景目录仍可使用；创建诊断运行会返回 `503`。

### 导入演示知识库

`data/knowledge/` 包含四份与预设故障场景对应的模拟 Runbook。启动 Milvus 并配置 OpenAI 兼容 Embedding 后，执行以下命令：

```dotenv
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=your-api-key
EMBEDDING_VECTOR_SIZE=1536
KNOWLEDGE_SOURCE_DIRECTORY=data/knowledge
```

```bash
docker compose up -d milvus
uv run python -m scripts.ingest_knowledge
```

`EMBEDDING_VECTOR_SIZE` 必须与 Embedding 模型输出维度一致。应用和脚本必须使用同一个 `KNOWLEDGE_SOURCE_DIRECTORY`。脚本按文件名顺序读取 Markdown，并使用 Milvus upsert，因此可以重复运行。

### 启动 GPUI 桌面工作台

先启动 FastAPI 服务。配置模型供应商后，工作台才能创建真实诊断运行。

```bash
uv run uvicorn app.api.main:app --reload
```

在另一个终端启动 GPUI 桌面应用。

```bash
cargo run --manifest-path frontend/Cargo.toml
```

桌面应用通过 `POST /api/v1/runs/stream` 创建实时诊断运行，并提供：

- 故障描述输入与实时安全轨迹；
- 等待用户输入时的补充信息提交与续跑；
- 高风险工具的两步审批：先记录决议，再显式确认续跑；
- 完成态诊断报告的纯文本展示。

桌面端不显示工具参数、原始工具观察结果、模型上下文或 Harness checkpoint 内容。

### 完整验收

```bash
cargo test --manifest-path frontend/Cargo.toml
cargo check --manifest-path frontend/Cargo.toml
uv lock --check
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

## 项目文档

- [项目设计](docs/PROJECT_SPEC.md)

## Git 提交约定

每个可验证的阶段成果创建一次提交。初始化阶段、Harness Core、RAG 与工具层、API 与持久化、前端与评测分别提交；不为零散文件修改单独提交。
