# OpsMind

OpsMind 是一个面向长流程任务的 Agent Harness 项目，使用运维故障诊断作为可复现 benchmark。

## 当前阶段

Harness Core：已完成共享契约、预算与动作策略、LangGraph Harness Loop，以及 Progress Verifier 的停滞控制。下一阶段接入 Context Manager、RAG 和模拟诊断工具。

## 技术栈

- Python 3.12
- LangChain、LangGraph、LangSmith
- FastAPI、PostgreSQL、Qdrant
- pytest、Ruff、mypy、Docker Compose

## 本地开发

### 前置条件

- 安装 Docker Desktop，并确保 Docker 服务正在运行。
- 安装 `uv`。项目使用 `uv` 管理 Python 版本、虚拟环境和依赖锁定文件。

### 初始化步骤

1. 复制环境变量示例文件。

   ```bash
   cp .env.example .env
   ```

2. 创建 Python 3.12 虚拟环境并安装依赖。

   ```bash
   uv sync --all-groups
   ```

3. 启动 PostgreSQL 和 Qdrant。

   ```bash
   docker compose up -d
   ```

4. 运行项目初始化验收。

   ```bash
   uv run pytest
   uv run ruff check .
   uv run mypy app
   docker compose config
   ```

## 项目文档

- [项目设计](docs/PROJECT_SPEC.md)

## Git 提交约定

每个可验证的阶段成果创建一次提交。初始化阶段、Harness Core、RAG 与工具层、API 与持久化、前端与评测分别提交；不为零散文件修改单独提交。
