# 第 37 阶段：模型实际用量预算

## 目标

将供应商返回的 Token 用量和估算成本接入 Harness。模型调用次数在请求前受控；输入 Token、输出 Token 和成本在响应后核算，并写入预算与轨迹。

## 数据流

- `LangChainActionProvider` 使用 `include_raw=True` 保留 LangChain 原始响应。
- Provider 从 `usage_metadata` 或 `response_metadata` 提取输入与输出 Token，并根据注入的每千 Token 价格计算估算成本。
- Provider 返回 `ModelInvocation`，其中包含 `AgentAction` 和 `ModelUsage`。
- Harness 兼容旧 Provider：仅返回 `AgentAction` 时，用量默认为零。

## 预算规则

- 每次模型请求前，Harness 先消费一个 `model_calls` 预算。
- 响应返回后，Harness 检查实际 Token 和成本是否超出剩余预算。
- 用量未超限时，Token 和成本写入 `BudgetState`，并在 `MODEL_CALLED.token_usage` 中保存完整审计数据。
- 用量超限时，Harness 写入 `MODEL_CALLED` 和 `ACTION_BLOCKED` 事件，并阻断候选动作。
- 已发生的模型调用仍计入 `used_model_calls`。超额 Token 和成本不写入受限预算，但保留在事件中，避免产生无效预算状态。

## 边界

- 默认价格为零。接入具体模型时，由调用方通过 `input_cost_per_1k_tokens` 和 `output_cost_per_1k_tokens` 提供价格。
- 供应商未返回用量时，Provider 返回零用量，不阻断既有本地测试或模拟 Provider。
- 本阶段不执行预估 Token 预留；因此实际用量只能在模型响应后确认。

## 验收

- `tests/test_langchain_action_provider.py` 验证原始响应用量提取和成本计算。
- `tests/test_harness_model_usage_budget.py` 验证正常预算消费、Token 超额阻断和成本超额阻断。
