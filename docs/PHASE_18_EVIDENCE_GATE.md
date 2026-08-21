# 第 18 阶段：最终回答证据门槛

## 目标

本阶段在 Harness 的 `finish` 节点增加独立证据门槛。模型提出 `final_answer` 后，即使动作已通过 Policy，也必须拥有满足门槛的结构化证据，才能进入 `COMPLETED`。

本阶段只校验证据数量，不判断证据质量、独立性、时效性或与结论的因果关系。

## 规则

- `EvidenceGate.min_evidence` 默认为 1，且必须大于 0。
- `final_answer` 前调用 `EvidenceGate.validate()` 检查状态中的 `EvidenceItem` 数量。
- 证据不足时，运行状态为 `BLOCKED`，错误写入状态，并记录 `VERIFICATION_FAILED` 事件。
- 证据满足门槛时，保留现有 `RUN_COMPLETED`、进度验证与完成状态逻辑。
- 证据门槛可通过 `HarnessLoop` 构造参数注入，以支持不同评测场景。

## 新增或调整模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/evidence.py` | 提供可配置的 `EvidenceGate`。 |
| `app/harness/loop.py` | 在 `final_answer` 的结束路径应用证据门槛。 |
| `tests/test_harness_evidence_gate.py` | 覆盖无证据阻断、满足门槛完成与自定义门槛。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
