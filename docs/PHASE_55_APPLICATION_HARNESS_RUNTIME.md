# 第 55 阶段：应用 Harness 运行时装配

本阶段将动作提供器、场景工具注册表和 Harness 策略装配为 `HarnessDiagnosisRunner`。应用工厂接收到 `action_provider` 后，`POST /api/v1/runs` 可以执行完整的受控诊断运行。

## 装配关系

1. 应用工厂创建或接收 `ScenarioStore`。
2. 应用工厂创建 `ToolRegistry` 并注册场景工具。
3. `create_harness_diagnosis_runner()` 将动作提供器、工具注册表和 `ActionPolicy` 传入 `HarnessLoop`。
4. `HarnessDiagnosisRunner` 为每次请求复制预算模板并启动新的 Harness 状态。

`LangChainActionProvider` 可以作为 `action_provider` 注入。测试使用固定动作提供器验证装配过程，不连接模型供应商。

## 默认预算

默认单次运行允许 12 个步骤、6 次工具调用、8 次模型调用、16,000 个 Token、120 秒运行时长和 0.1 美元估算成本。每次运行使用预算副本，不共享已消耗值。

## 边界

默认应用未注入动作提供器时，运行 API 继续返回 `503`。调用方可以注入完整的 `diagnosis_runner`，也可以注入 `action_provider` 让应用构建标准 Harness；两者不能同时提供。
