# 第 51 阶段：应用工具注册表装配

本阶段在 FastAPI 应用工厂中创建场景工具注册表。注册表与同一应用实例的场景存储绑定，后续诊断运行接口可通过依赖注入取得已校验的只读工具。

## 装配关系

`create_app()` 按以下顺序完成装配：

1. 使用显式注入的 `ScenarioStore`，或创建默认场景存储。
2. 创建新的 `ToolRegistry`。
3. 注册 `query_logs`、`query_metrics` 和 `query_topology`。
4. 将注册表保存到 `app.state.tool_registry`。

`get_tool_registry()` 只读取应用状态。路由不应在请求处理中重新注册工具或创建新的场景存储。

## 边界

- 工具只读取固定场景数据，不访问生产系统。
- 每个应用实例拥有独立的注册表和场景数据引用。
- 本阶段不新增诊断运行 HTTP 接口，也不创建或调用模型。

## 验证

`tests/test_application_tool_registry.py` 验证注入场景能被应用注册的指标工具读取，并验证两个应用实例之间不共享工具注册表或场景数据。
