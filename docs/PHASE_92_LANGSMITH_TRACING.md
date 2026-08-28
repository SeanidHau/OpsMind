# LangSmith 运行追踪

本阶段为每个 Harness 运行提供可选的 LangSmith 根 Trace。实现复用 LangChain 和 LangGraph 的现有追踪能力，不维护独立事件同步链路。

## 启用条件

在 `.env` 或部署环境中设置以下变量：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-langsmith-api-key
LANGSMITH_PROJECT=opsmind-dev
```

`LANGSMITH_TRACING=false` 时，应用不创建 LangSmith Client，也不改变 Harness 的运行路径。`LANGSMITH_TRACING=true` 且未配置 `LANGSMITH_API_KEY` 时，配置加载失败，避免静默丢失 Trace。

## Trace 结构

每次新建运行、用户输入续跑、记录审批决议或获批续跑都会创建名为 `opsmind.harness_run` 的根 Trace。根 Trace 使用 `chain` 类型，并包含以下信息：

- 标签：`opsmind`、`harness` 和操作类型；
- 元数据：`operation`、`run_id`、`session_id`、`thread_id` 和 `harness_profile`；
- 输入：用户故障描述；
- 输出：终止状态、步骤数、工具调用数、模型调用数、Token 消耗和错误数量。

消融基准将 `harness_profile` 写入元数据，因此可在同一 LangSmith 项目中按 `full`、`without_context_manager` 和 `without_progress_verifier` 过滤和比较运行。

## 数据边界

根 Trace 不重复写入工具原始观察结果或最终报告。LangGraph、LangChain 和模型供应商的子 Trace 仍可能包含模型提示词、工具参数和工具结果。

在真实生产环境启用前，确认发送到 LangSmith 的数据符合组织的数据处理、保留期限和访问控制要求。项目不在 Trace 中写入 API Key、数据库连接串或其他 `Settings` 密钥字段。
