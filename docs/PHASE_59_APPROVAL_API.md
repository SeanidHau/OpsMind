# 第 59 阶段：高风险动作审批 API

本阶段提供两步审批流程，防止提交审批决议时直接执行高风险工具。

1. 调用 `POST /api/v1/runs/{run_id}/approval`，提交 `ApprovalCommand`。
2. 对 `approve` 或 `edit` 决议，调用 `POST /api/v1/runs/{run_id}/approval/resume`，从已保存的决议 checkpoint 续跑。

## 决议契约

审批请求复用 `ApprovalCommand`。`decision` 支持 `approve`、`edit` 和 `reject`，每种决议都必须提供 `reason`。`edit` 还必须提供与待审批工具同名的 `edited_action`。

决议接口只记录审计状态。它不创建新运行，不调用模型，也不执行工具。拒绝决议将运行标记为 `blocked`，不能继续通过获批续跑接口执行。

## 错误处理

未知 `run_id` 返回 `404`。运行不处于等待审批状态时，决议接口返回 `409` 和 `run cannot accept approval`；运行没有已批准决议时，续跑接口返回 `409` 和 `run cannot resume approved action`。

应用未配置相应能力时，决议接口返回 `503` 和 `diagnosis approval resolver is not configured`；获批续跑接口返回 `503` 和 `approved diagnosis run resumer is not configured`。
