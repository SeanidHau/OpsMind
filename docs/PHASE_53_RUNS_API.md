# 第 53 阶段：诊断运行 API

本阶段提供 `POST /api/v1/runs`。接口只接收会话标识、线程标识和诊断任务文本，并委托应用工厂注入的诊断运行器执行。

## 请求字段

- `session_id`：会话标识。
- `thread_id`：线程标识。
- `user_query`：诊断任务文本。

## 响应内容

响应包含运行 ID、终止或暂停状态、步骤数、最终回答、待回答问题和错误列表。接口不返回内部模型上下文、工具处理函数或原始场景数据。

## 装配边界

`HarnessDiagnosisRunner` 为每次请求创建独立的初始状态和预算副本，再调用 `HarnessLoop`。因此一次运行的预算消耗不会影响后续运行。

默认应用尚未配置模型驱动的运行器。此时调用 `POST /api/v1/runs` 返回 `503` 和 `diagnosis runtime is not configured`，不会返回伪造诊断结果。后续阶段将配置 LangChain 动作提供器，并将完整 Harness 注入应用工厂。
