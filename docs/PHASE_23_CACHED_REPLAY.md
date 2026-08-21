# 第 23 阶段：无副作用 Cached Replay

## 目标

基于第 22 阶段归档的运行快照提供只读回放。该能力用于检查历史轨迹、调试 Prompt 和复现失败样本，不重新执行模型、工具或 LangGraph。

## 规则

- `ReplayMode.CACHED` 表示回放完全复用历史快照。
- `CachedReplayService` 仅调用 `RunArchive.load()`，不依赖模型或工具执行器。
- 回放返回 `ReplayResult`，包含源运行 ID、终止状态、最终状态和历史轨迹。
- 回放结果和归档之间保持深拷贝隔离；修改回放结果不会改变已保存的快照。
- 不存在的运行 ID 必须抛出明确的 `KeyError`。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/replay.py` | 提供只读 `CachedReplayService`。 |
| `tests/test_cached_replay.py` | 覆盖历史复用、数据隔离、未知运行和无模型/工具调用。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
