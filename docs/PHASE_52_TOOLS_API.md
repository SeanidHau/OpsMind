# 第 52 阶段：工具目录 API

本阶段提供 `GET /api/v1/tools`。工作台可以在提交诊断任务前读取当前应用实例支持的工具和参数约束。

## 响应内容

每项工具摘要包含以下字段：

- `name`：工具名称。
- `risk_level`：风险等级。
- `read_only`：工具是否只读。
- `requires_approval`：工具策略是否要求人工审批。
- `required_args`：必填参数名称。
- `allowed_args`：允许的参数名称；`null` 表示尚未声明参数 schema。
- `max_calls_per_run`：单次运行的调用上限；`null` 表示未设置上限。

接口按工具名称排序。它不返回工具处理函数、场景原始数据或任何执行结果。

## 当前工具

`query_logs`、`query_metrics` 和 `query_topology` 都绑定到固定场景数据，风险等级为 `low`，并声明为只读工具。

## 边界

本阶段不执行工具，不创建诊断运行，也不调用模型。工具目录只反映应用工厂已注册的策略，路由不会创建新的注册表。
