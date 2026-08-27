# 第 66 阶段：GPUI 桌面工作台壳

本阶段以 GPUI 替换 Streamlit 作为 OpsMind 的正式前端技术栈。桌面工程位于 `frontend/`，与 Python 后端保持独立构建。

## 启动步骤

1. 确认已安装最新稳定版 Rust 和 macOS Xcode 命令行工具。
2. 运行 `cargo run --manifest-path frontend/Cargo.toml`。

应用启动后显示控制台布局、FastAPI 默认地址和运行轨迹区域。当前阶段不发起诊断请求。

## 后续接入

后续阶段会从 GPUI 接入 `POST /api/v1/runs/stream`，并增加故障描述输入、SSE 时间线、用户补充信息和高风险动作审批。桌面端只消费后端已投影的安全事件字段。

## 版本约束

工程固定 `gpui = "0.2.2"`。GPUI 仍处于 pre-1.0，升级版本前必须重新编译桌面工程并验证交互行为。
