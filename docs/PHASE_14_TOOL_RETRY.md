# 第 14 阶段：受预算约束的工具重试

## 目标

本阶段让 Harness 对瞬态工具错误执行受控重试。重试复用当前已获策略批准的工具动作，不重复调用模型，也不重新执行 Policy；每次实际尝试都受到工具调用预算约束。

本阶段不实现指数退避、错误类型白名单或跨进程重试。重试在同一次 LangGraph 运行内立即执行。

## 重试规则

- `max_tool_retries` 可以为 0，且不得为负数。
- 初次执行失败后，若失败次数未超过上限且工具预算足够，则直接重试当前动作。
- 每次实际工具尝试都会增加 `tool_call_count`；重试额外消费一个 `tool_calls` 预算。
- 重试不增加 `step_count`，也不再次调用 `ActionProvider` 或 `ActionPolicy`。
- 重试成功后将 `retry_count` 重置为 0，再进入进度验证。
- 重试上限耗尽时，运行状态为 `FAILED`；重试会超出工具预算时，运行状态为 `BLOCKED`。
- 每次可继续的失败写入 `TOOL_RETRY` 事件；最终失败仍写入 `RUN_FAILED` 事件。

## 新增或调整模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/loop.py` | 在工具执行节点处理失败、预算消费和回边路由。 |
| `app/models/contracts.py` | 增加 `TOOL_RETRY` 轨迹事件类型。 |
| `tests/test_harness_tool_retry.py` | 覆盖成功重试、上限耗尽、预算拦截和参数边界。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
