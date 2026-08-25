# 第 47 阶段：集中配置层

## 目标

使用 `pydantic-settings` 集中加载和校验运行配置。路由、Harness 和基础设施模块后续通过注入的 `Settings` 使用配置，不自行读取环境变量。

## 配置来源

本地开发按以下优先级读取配置：

1. 真实环境变量。
2. 项目根目录的 `.env` 文件。
3. `Settings` 中的默认值。

`.env.example` 仅提供字段示例，不应提交真实密钥。

## 已支持的配置

- `APP_ENV`：`development`、`test` 或 `production`。
- `LOG_LEVEL`：应用日志级别。
- `DATABASE_URL`、`QDRANT_URL`：基础设施地址。当前阶段只校验格式，不建立连接。
- `LLM_PROVIDER`、`LLM_MODEL`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`：模型供应商配置。
- `LANGSMITH_TRACING`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT`：LangSmith 配置。

空字符串形式的可选模型和 LangSmith 配置会转换为 `None`，避免被误判为有效值。

## 应用集成

`create_app(settings=...)` 支持注入测试或部署配置，并将解析后的实例保存到 `app.state.settings`。

当 `APP_ENV=production` 时，FastAPI 的 `debug` 关闭。配置中的密钥和完整数据库地址不得写入健康检查或其他公开响应。
