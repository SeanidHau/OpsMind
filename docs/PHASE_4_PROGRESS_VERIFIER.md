# 第 4 阶段：Progress Verifier 与停滞控制

## 目标

本阶段为 Harness Loop 增加独立的 `ProgressVerifier`。验证器在每个成功工具调用后评估本轮是否产生新观察结果，并在连续停滞时请求重新规划或强制终止。

## 规则

- 新的「动作 + 观察结果」指纹表示 `progressed`。
- 相同工具可以多次调用。只有动作和观察结果都相同时，才视为 `stalled`。
- `final_answer` 表示 `completed`。
- 连续两次 `stalled` 时，状态写入 `replan_requested=True`。
- 连续三次 `stalled` 时，Loop 写入 `stalled` 终止状态和 `verification_failed` 事件。

重新规划节点尚未实现。第 4 阶段只产生可审计的重规划请求，并继续交给动作提供器产生后续动作。

## 变更范围

| 路径 | 职责 |
| --- | --- |
| `app/harness/progress.py` | 生成进度评估和稳定指纹。 |
| `app/harness/loop.py` | 在工具执行后插入 `verify_progress` 节点。 |
| `app/models/contracts.py` | 定义 `ProgressAssessment` 和运行时状态字段。 |

## 图路由

```text
execute_tool → verify_progress → propose_action
                            └→ finish (连续三次停滞)
```

策略拦截、工具失败和审批暂停仍直接结束当前图，不进入 Progress Verifier。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

验收测试覆盖新观察、相同工具返回新结果、重复观察的阈值行为、`final_answer` 的完成状态，以及 Loop 的强制终止路由。
