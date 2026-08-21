# 第 15 阶段：结构化证据链

## 目标

本阶段将成功的工具观察结果转换为稳定、可引用的 `EvidenceItem`。证据拥有确定性 ID、工具来源、受长度限制的标准化内容和截断标记；Harness 将证据写入状态、轨迹和下一轮模型上下文。

本阶段不解释指标含义、不判断根因，也不生成最终诊断报告。证据内容是工具观察结果的规范化表示，不代表模型结论。

## 证据规则

- 对工具名称和观察结果执行稳定 JSON 序列化，字典键顺序不影响证据 ID。
- `evidence_id` 使用工具名称与完整规范化观察结果的 SHA-256 哈希生成。
- `content` 受 `max_content_chars` 限制；超出时只截断展示内容，ID 仍基于完整观察结果。
- 同一运行内遇到相同 `evidence_id` 时不重复写入状态。
- 每个新证据写入 `EVIDENCE_COLLECTED` 事件。
- Context Manager 以 `evidence:<evidence_id>` 作为引用，并优先展示证据内容而非原始工具结果。

## 新增或调整模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/evidence.py` | 观察结果规范化、哈希、长度限制与证据构造。 |
| `app/harness/loop.py` | 成功工具调用后采集并去重证据，写入轨迹。 |
| `app/harness/context.py` | 使用稳定证据 ID 和内容构建模型上下文。 |
| `app/models/contracts.py` | 定义 `EvidenceItem` 与 `EVIDENCE_COLLECTED` 事件。 |
| `tests/test_evidence_collector.py` | 覆盖稳定 ID、截断、Loop 集成和参数边界。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
