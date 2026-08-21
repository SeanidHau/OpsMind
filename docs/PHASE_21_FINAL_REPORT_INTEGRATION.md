# 第 21 阶段：最终报告接入 Harness

## 目标

将结构化诊断报告接入 `final_answer` 的完成路径。Harness 在完成前校验证据门槛和报告引用，成功后同时保存机器可读报告与面向运维人员的 Markdown。

## 规则

- `AgentAction.action_type` 为 `final_answer` 时必须包含 `DiagnosisReport`。
- 非 `final_answer` 动作不得携带报告。
- Harness 先执行 `EvidenceGate`，再验证报告引用并渲染 Markdown。
- 证据不足、重复引用或未知引用都会将运行置为 `BLOCKED`，并记录 `VERIFICATION_FAILED` 事件。
- 成功完成后，状态中的 `diagnosis_report` 保存结构化对象，`diagnosis` 保存其 JSON 兼容字典，`final_answer` 保存 Markdown。

## 验收范围

| 场景 | 预期结果 |
| --- | --- |
| 最终回答缺少报告 | 契约校验失败。 |
| 报告引用当前运行的证据 | 运行完成，并保存两种报告表示。 |
| 报告引用未知证据 | 运行阻断并记录验证失败事件。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
