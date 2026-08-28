# 第 85 阶段：RAG 报告来源引用

## 目标

本阶段在最终 Markdown 诊断报告中增加「知识来源」部分。报告只从已引用的 `query_knowledge` 证据中读取 `source_id`，并按出现顺序去重。

## 渲染规则

- 仅处理报告 `evidence_ids` 实际引用的证据。
- 仅处理 `tool_name` 为 `query_knowledge` 的证据。
- 仅提取 `hits[].source_id`，不把检索结果重新解释为新的诊断结论。
- 证据 JSON 无效、被截断或不含 `hits` 时，保留原有证据引用，不显示知识来源部分。

来源引用不改变 `DiagnosisReport` 契约、Evidence Gate 或报告引用校验。模型仍必须引用当前运行已经生成的证据 ID。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
