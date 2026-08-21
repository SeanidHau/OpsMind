# 第 16 阶段：LangChain 结构化动作提供器

## 目标

本阶段实现 `LangChainActionProvider`。它将 `ContextManager` 已构建的最小上下文转换为 LangChain 消息，并使用聊天模型的 `with_structured_output(AgentAction)` 生成下一步候选动作。

模型只能提出 `AgentAction`，不能直接执行工具。工具权限、预算、审批和重试仍由 Harness Loop 控制。

## 输入与输出边界

- 提供器只读取 `user_query` 和 `model_context`，不向模型传递预算、完整轨迹、内部错误对象或完整状态。
- 系统消息说明动作边界：证据不足时继续收集或追问，不得编造观察结果。
- 用户消息以稳定 JSON 包含用户问题、上下文条目和 `truncated` 标记。
- 模型返回 `AgentAction` 时直接使用；返回字典时通过 `AgentAction.model_validate()` 校验。
- 未构建 `model_context` 时拒绝调用模型。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/agents/action_provider.py` | LangChain 消息构造、结构化输出绑定和动作校验。 |
| `tests/test_langchain_action_provider.py` | Schema 绑定、上下文边界、字典校验与缺失上下文测试。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
