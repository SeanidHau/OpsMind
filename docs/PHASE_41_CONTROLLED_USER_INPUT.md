# 第 41 阶段：受控澄清追问与恢复

## 目标

为 `ask_user` 动作增加暂停、归档和恢复语义。Harness 不直接假设缺失信息，而是保存明确问题，等待用户回答后从 checkpoint 继续诊断。

## 动作契约

`ask_user` 必须携带 `question` 字段。其他动作不能携带该字段。

问题由模型提出，但 Harness 负责控制次数、保存状态和恢复运行。模型不能自行修改会话或跳过等待步骤。

## 执行顺序

1. 模型提出 `ask_user` 动作。
2. Policy 检查步骤预算。
3. Harness 检查追问次数上限。
4. Harness 保存 `pending_question`，写入 `RUN_PAUSED`，状态变为 `WAITING_USER_INPUT`。
5. 外部调用 `resume_with_user_input(run_id, answer)`。
6. Harness 写入问题、用户回答和 `RUN_RESUMED` 事件。
7. Context Manager 将最近对话加入下一轮最小模型上下文。
8. Harness 继续构建上下文、调用模型和执行后续动作。

## 追问上限

`HarnessLoop.max_user_questions` 默认值为 `2`。每次进入等待输入状态时，`question_count` 加一。

达到上限后，新的 `ask_user` 动作会被阻断：

- 原因：`本次运行已达到澄清追问上限。`
- 违规标识：`question_limit`

被阻断的动作不会增加 `question_count` 或消费步骤预算。

## 上下文边界

Harness 保存完整会话，但 Context Manager 每轮最多放入最近四条对话。用户回答以 `conversation` 来源进入最小上下文，模型无法直接读取完整 checkpoint。

## 验收覆盖

- `tests/test_contracts.py` 验证 `question` 字段的动作契约。
- `tests/test_context_manager.py` 验证最近对话进入最小上下文。
- `tests/test_harness_user_input.py` 验证暂停、恢复、上下文传递、次数上限和空回答拒绝。
