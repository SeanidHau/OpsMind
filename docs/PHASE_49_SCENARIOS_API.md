# 第 49 阶段：场景目录 API

## 目标

提供 `GET /api/v1/scenarios`，供工作台查询可选择的故障诊断场景。接口只返回摘要，原始日志和指标数值继续保留在受控场景存储中。

## 响应字段

每个场景摘要包含：

- `scenario_id`：稳定场景标识。
- `service`：后续工具调用使用的服务名称。
- `log_count`：场景日志数量。
- `metric_names`：可用指标名称，按字母顺序返回。
- `dependency_count`：依赖数量。

接口不会返回日志正文、日志时间、指标数值或完整依赖列表。

## 注入与默认行为

`create_app(scenario_store=...)` 接受显式的 `ScenarioStore`。未注入场景存储时，应用使用空目录，`GET /api/v1/scenarios` 返回空数组。

后续场景加载器可以替换工厂注入的存储实现，不需要修改路由契约。

## 数据隔离

`ScenarioStore.list_scenarios()` 返回内部场景的深拷贝，并按 `scenario_id` 稳定排序。调用方修改返回对象不会影响后续工具调用使用的场景数据。
