# 第 20 阶段：可追溯诊断报告渲染

## 目标

将 `DiagnosisReport` 和本次运行的 `EvidenceItem` 渲染为固定结构的 Markdown，供运维人员阅读和复核。

## 规则

- 渲染前必须调用 `DiagnosisReportValidator` 校验证据引用。
- 报告只展示 `evidence_ids` 中明确引用的证据，不附带未引用的观察结果。
- 输出包含摘要、候选原因、置信度、证据引用和建议操作五个部分。
- 证据 ID、工具名称和原始证据内容会保留在报告中，便于追溯。
- 未知或重复的证据引用会使渲染失败；本阶段不生成报告，也不保存报告。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/report_renderer.py` | 提供 `MarkdownReportRenderer`，校验并渲染报告。 |
| `tests/test_markdown_report_renderer.py` | 覆盖固定章节、引用筛选和未知引用拒绝。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
