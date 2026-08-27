# 第 57 阶段：多提供器模型装配

本阶段将模型创建逻辑抽象为 `ChatModelProvider`。当前内置 `openai` 和 `anthropic` 两种提供器，后续厂商可通过同一协议注册，无需修改 Harness 或 API 路由。

## 通用配置

`LLM_PROVIDER` 指定提供器，`LLM_MODEL` 指定模型。`LLM_API_KEY` 和 `LLM_BASE_URL` 是通用回退字段。

提供器专用字段优先级更高：

| 提供器 | 专用密钥 | 专用基础地址 |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | `OPENAI_BASE_URL` |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` |

提供器专用字段未设置时，工厂回退到 `LLM_API_KEY` 和 `LLM_BASE_URL`。

## 网关兼容范围

`LLM_PROVIDER=openai` 使用 OpenAI Chat Completions 兼容协议，适用于提供该协议的网关。`LLM_PROVIDER=anthropic` 使用 Anthropic Messages 兼容协议。两个协议不同，基础地址不能在两者之间互换。

## 安全边界

模型客户端只用于提出结构化动作。工具调用、策略检查、预算、审批和终止控制继续由 Harness 执行。测试只构造客户端替身，不发送模型请求或输出 API 密钥。
