# 第 24 阶段：确定性 Trajectory Evaluation

## 目标

对归档运行快照执行无需 LLM 的离线质量评测。评测结果用于回归测试、失败样本筛选和后续与 LangSmith 实验结果对照。

## 检查项

- 轨迹最后一项必须是 `CHECKPOINT_SAVED`。
- 终止状态必须存在相匹配的业务终止事件。
- 快照中的预算状态必须仍满足全部上限。
- 已完成运行的诊断报告必须能引用当前快照中的证据。

每项检查输出名称、通过状态和说明。总分为通过检查数除以检查总数；只有所有检查通过时，评测结果才通过。

## 范围限制

本阶段不调用模型、不执行工具，也不判断自然语言结论的专业正确性。模型质量与业务根因正确率将在离线样本集和 LangSmith 评测阶段覆盖。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/harness/evaluation.py` | 评测归档轨迹的结构与安全不变量。 |
| `tests/test_trajectory_evaluation.py` | 覆盖合格轨迹及 checkpoint、预算和引用失败。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
