# 第 74 阶段：GPUI 高风险动作审批

本阶段在桌面端接入高风险工具的两步审批流程。运行状态为 `waiting_approval` 时，后端在运行摘要中返回 `pending_approval`。摘要只包含 `tool_name` 和 `reason`。

## 审批流程

1. Harness 暂停运行，并返回 `waiting_approval` 和 `pending_approval`。
2. 桌面端校验 `run_id`、工具名称和策略原因后，显示审批面板。
3. 操作人员填写审批理由，选择“记录批准”或“拒绝动作”。
4. 桌面端调用 `POST /api/v1/runs/{run_id}/approval`。该请求只记录决议，不执行工具。
5. 记录批准后，界面显示“确认并继续”。操作人员必须再次选择该按钮。
6. 桌面端调用 `POST /api/v1/runs/{run_id}/approval/resume`，从已批准的 checkpoint 继续运行。

拒绝动作不会调用获批续跑接口。

## 数据与执行边界

`pending_approval` 不包含工具参数、原始观察结果、模型上下文或 checkpoint 内容。运行摘要中的审批请求缺少必填字段、字段为空或超出长度限制时，桌面端不显示审批控件。

审批理由不能为空，且最多为 2000 个字符。审批请求和获批续跑请求均在 GPUI 后台执行器中执行。前台线程只处理安全摘要、状态更新和重绘。

## 验证范围

API 测试覆盖审批摘要仅投影 `tool_name` 和 `reason`。Rust 测试覆盖审批摘要和审批理由的长度限制，以及“记录审批”和“获批续跑”使用两个不同 HTTP 请求的行为。
