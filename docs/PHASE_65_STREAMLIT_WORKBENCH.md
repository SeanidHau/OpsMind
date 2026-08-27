# 第 65 阶段：Streamlit 运维诊断工作台

本阶段提供 `frontend/app.py`。工作台用于提交故障描述、读取场景目录、展示运行中 SSE 时间线和最终诊断摘要。

## 启动步骤

1. 启动 FastAPI 服务：`uv run uvicorn app.api.main:app --reload`。
2. 在另一个终端启动工作台：`uv run streamlit run frontend/app.py`。
3. 在侧边栏确认 API 地址。默认地址是 `http://127.0.0.1:8000`。
4. 输入故障描述，选择「启动受控诊断」。

## 数据边界

工作台调用 `POST /api/v1/runs/stream`。SSE 客户端只解析后端已投影的安全事件字段。时间线不显示工具参数、工具原始观察结果、模型输入摘要或完整动作内容。

场景目录来自 `GET /api/v1/scenarios`，只用于帮助选择诊断上下文。工作台不会把场景原始日志或指标数据写入浏览器会话。

## 失败处理

工作台无法连接 API、后端返回非 JSON SSE 数据或运行连接中断时，会显示错误状态。此时可以检查 FastAPI 服务地址、模型供应商配置和服务日志后重新提交。
