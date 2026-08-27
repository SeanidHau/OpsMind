# 第 54 阶段：诊断运行查询 API

本阶段提供 `GET /api/v1/runs/{run_id}`。接口读取 Harness 已归档的运行快照，供工作台在创建运行后重新获取状态。

## 响应内容

接口返回运行 ID、终止或暂停状态、步骤数、最终回答、待回答问题和错误列表。接口不返回模型上下文、工具处理函数、原始场景数据或完整轨迹。

## 查询行为

`HarnessDiagnosisRunner.get_run()` 使用 Harness 的缓存回放能力读取快照。查询不会再次调用模型、工具或 LangGraph 节点。

未知的 `run_id` 返回 `404` 和 `run not found`。应用未配置支持快照读取的运行器时，接口返回 `503` 和 `diagnosis run reader is not configured`。

## 存储范围

当前归档使用进程内存。服务重启后，内存中的运行快照不会保留。后续阶段将把 `RunArchive` 替换为持久化实现。
