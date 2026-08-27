# 第 58 阶段：用户输入续跑 API

本阶段提供 `POST /api/v1/runs/{run_id}/input`。当 Harness 将运行暂停为 `waiting_user_input` 后，调用方通过此接口提交一条回答，并恢复同一运行。

## 请求和响应

请求体包含 `answer`。接口会去除首尾留白，空白回答返回 `422`，不会写入运行历史。

成功响应与运行查询接口一致，包含运行 ID、状态、步骤数、最终回答、待回答问题和错误列表。恢复后的运行继续使用原始 `run_id`。

## 恢复条件

只有等待用户输入的已归档运行可以恢复。未知 `run_id` 返回 `404`。运行不处于等待输入状态时，接口返回 `409` 和 `run cannot accept user input`。

应用未配置支持续跑的运行器时，接口返回 `503` 和 `diagnosis run resumer is not configured`。

## 执行边界

路由不创建新的 Harness、不直接调用模型，也不修改工具策略。`HarnessDiagnosisRunner` 将回答交给 `HarnessLoop.resume_with_user_input()`，由 Harness 继续执行后续模型、策略和工具节点。
