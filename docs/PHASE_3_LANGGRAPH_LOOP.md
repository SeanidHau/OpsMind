# 第 3 阶段：LangGraph Harness Loop 骨架

## 目标

本阶段将第 1、2 阶段的动作契约、预算和策略接入真实的 LangGraph `StateGraph`。图使用可替换的动作提供器和工具执行器完成最小闭环，不依赖特定模型或外部工具。

本阶段不接入真实 LLM、RAG、checkpoint 持久化、Progress Verifier 或审批恢复。这些能力在后续阶段实现。

## 新增模块

| 路径 | 职责 |
| --- | --- |
| `app/harness/loop.py` | 定义图状态工厂、依赖协议和 `HarnessLoop`。 |
| `tests/test_harness_loop.py` | 验证图路由、预算消费、策略拦截和审计事件。 |

`app/models/contracts.py` 新增 `HarnessStatus`，并为 `DiagnosisState` 添加 Loop 运行时字段：`current_action`、`policy_decision` 和 `terminal_status`。

## 图结构

```text
START → propose_action → policy_check
                           ├─ allow + call_tool → execute_tool ─┐
                           ├─ allow + final_answer → finish     │
                           ├─ require_approval → finish         │
                           └─ block → finish                    │
                                      ▲                           │
                                      └───────────────────────────┘
```

`policy_check` 必须发生在 `execute_tool` 之前。只有策略结果为 `allow` 时，Loop 才通过 `BudgetManager.consume` 写入新的预算状态。

## 依赖边界

`ActionProvider` 仅负责根据当前状态异步提出 `AgentAction`。未来的 LangChain 模型适配器实现该协议。

`ToolExecutor` 仅负责异步执行已经获准的 `call_tool` 动作并返回观察结果。它不负责风险、预算或权限判断。

`HarnessLoop` 是唯一编排者，负责调用策略、更新状态和写入 `AgentEvent`。模型和工具不能绕过此层。

## 状态与终止语义

| `HarnessStatus` | 含义 |
| --- | --- |
| `completed` | 收到并允许执行 `final_answer`。 |
| `blocked` | 策略拒绝动作或预算不足。 |
| `waiting_approval` | 高风险工具需要人工审批。 |
| `failed` | 工具执行器抛出异常。 |

高风险工具进入 `waiting_approval` 时，不执行工具，也不消费候选动作预算。审批恢复将在 checkpoint 阶段实现。

## 事件要求

- 动作提供器返回动作后，写入 `action_proposed`。
- 工具执行前后分别写入 `tool_started` 和 `tool_finished`。
- 工具结果写入 `tool_results` 后，写入 `observation_recorded`。
- 策略阻断写入 `action_blocked`；审批暂停写入 `run_paused`；正常结束写入 `run_completed`。

## 验收标准

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

验收测试必须覆盖：

1. 已注册低风险工具执行后，Loop 能处理 `final_answer` 并结束。
2. 高风险工具进入待审批状态，工具执行器不会被调用。
3. 未注册工具在执行前被阻断并留下审计事件。
4. 预算耗尽后，下一轮动作被阻断，图不会无限循环。
