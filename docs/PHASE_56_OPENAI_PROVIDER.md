# 第 56 阶段：OpenAI 动作提供器装配

本阶段支持将 OpenAI 聊天模型装配为 `LangChainActionProvider`。模型仍只负责提出 `AgentAction`；工具执行、策略检查、预算和终止条件继续由 Harness 控制。

## 配置要求

设置 `LLM_PROVIDER=openai` 时，必须同时设置 `LLM_MODEL` 和 `OPENAI_API_KEY`。可选的 `OPENAI_BASE_URL` 用于兼容 OpenAI API 网关。

未设置 `LLM_PROVIDER` 时，应用不创建模型客户端，`POST /api/v1/runs` 继续返回 `503`。当前仅支持 `openai`；其他提供器会在应用创建时返回配置错误。

## 运行参数

模型以 `temperature=0` 创建，降低同一输入在演示和离线评测中的采样差异。模型名称不在代码中设置默认值，必须由部署配置明确提供。

## 安全边界

测试使用 `ChatOpenAI` 替身，不发送 API 请求，也不记录 API 密钥。应用日志和 HTTP 响应不包含 `OPENAI_API_KEY`。
