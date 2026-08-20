# 第 7 阶段：可复现故障场景与诊断工具

## 目标

本阶段实现固定故障场景数据，并为 `query_logs`、`query_metrics` 和 `query_topology` 注册只读工具。所有工具从同一 `IncidentScenario` 读取数据，保证演示、测试和后续评测的输入一致。

本阶段不查询真实基础设施，也不写入外部系统。未知服务必须返回 `ToolExecutionError`，不能以空结果伪造不存在的证据。

## 场景模型

`IncidentScenario` 包含：

- `scenario_id`：稳定场景标识。
- `service`：用于工具查询的服务名称；同一 `ScenarioStore` 中必须唯一。
- `logs`：预设结构化日志。
- `metrics`：预设数值指标。
- `dependencies`：预设服务依赖。

## 工具参数和结果

| 工具 | 必填参数 | 可选参数 | 结果 |
| --- | --- | --- | --- |
| `query_logs` | `service` | `contains` | 匹配日志和数量。 |
| `query_metrics` | `service` | 无 | 指标字典。 |
| `query_topology` | `service` | 无 | 依赖列表。 |

三个工具均为低风险只读工具，注册表将其定义投影为 `ToolPolicy`。它们仍必须经过 Harness 的 `ActionPolicy` 和预算检查后才能执行。

## 验收

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

验收覆盖日志关键词筛选、指标和拓扑查询、未知服务错误，以及 Harness Loop 的端到端执行。
