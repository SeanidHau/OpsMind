# 第 22 阶段：运行快照与进程内归档

## 目标

在 Harness 结束时将最终状态和完整轨迹保存为可 JSON 化的运行快照。快照为后续 cached replay、持久化和轨迹评测提供稳定输入。

## 规则

- `RunSnapshot` 包含运行、会话、线程、终止状态、最终状态和轨迹。
- `final_state` 不重复保存 `trajectory`；所有值在构建时转换为 JSON 兼容类型。
- Harness 在运行结束后追加 `CHECKPOINT_SAVED` 事件，再构建并保存快照。
- `InMemoryRunArchive` 拒绝同一 `run_id` 的重复保存，并在保存和读取时深拷贝，隔离嵌套状态修改。
- 本阶段仅提供进程内归档，不提供数据库持久化或实际回放执行。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/snapshot.py` | 构建运行快照，并提供可替换的归档接口。 |
| `tests/test_run_snapshot.py` | 覆盖 JSON 化、归档隔离、重复保存和 Loop 自动归档。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
