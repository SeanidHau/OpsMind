# 第 46 阶段：FastAPI 服务骨架

## 目标

建立 OpsMind 的 HTTP 服务入口，并固定应用工厂、API 版本和基础健康检查契约。

## 接口

`GET /api/v1/health` 返回服务进程状态：

```json
{
  "status": "ok",
  "service": "opsmind",
  "version": "0.1.0"
}
```

健康检查不验证 PostgreSQL、Qdrant 或模型供应商连通性。后续基础设施接入后，再增加独立的就绪检查接口。

## 启动方式

```bash
uv run uvicorn app.api.main:app --reload
```

启动后访问 `http://127.0.0.1:8000/docs` 查看 OpenAPI 文档。
