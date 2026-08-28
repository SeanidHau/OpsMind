# GitHub Actions CI

本阶段在 `.github/workflows/ci.yml` 中定义持续集成工作流。工作流在推送到 `main` 或创建 Pull Request 时运行。

## Python 任务

Python 任务使用 `uv` 安装项目锁定依赖，并按以下顺序检查：

1. `uv lock --check`
2. `uv run pytest`
3. `uv run ruff check .`
4. `uv run ruff format --check .`
5. `uv run mypy app`
6. `docker compose config --quiet`

## Rust 任务

Rust 任务使用稳定工具链，并检查 GPUI 桌面端的格式、单元测试和编译：

1. `cargo fmt --manifest-path frontend/Cargo.toml --check`
2. `cargo test --manifest-path frontend/Cargo.toml`
3. `cargo check --manifest-path frontend/Cargo.toml`

## 不在 CI 中执行的任务

`scripts.run_benchmark` 需要模型供应商凭据，且可能产生 Token 成本。知识库入库和 Milvus 检索评测需要外部基础设施。CI 不执行这些命令；在受控环境中手动运行并保存结果后，使用 `scripts.compare_benchmark_results` 比较 Harness 配置。
