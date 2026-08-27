# 第 67 阶段：GPUI SSE 协议解析器

本阶段在 `frontend/src/sse.rs` 实现了 FastAPI 安全 SSE 事件的 Rust 解析器。解析器按后端发送顺序返回事件名和 JSON 对象数据。

解析器只接受 JSON 对象。空数据、注释行和未识别字段不会生成时间线事件。非 JSON 数据或非对象 JSON 会返回错误，桌面端不能将错误负载当成正常运行事件。

后续 GPUI 网络适配器将把 `POST /api/v1/runs/stream` 的响应交给这个解析器，并把 `run_started`、运行轨迹事件、`run_finished` 和 `run_failed` 投递到桌面端状态。
