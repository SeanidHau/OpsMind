"""系统级路由。"""

import asyncio
from typing import Literal, cast

from fastapi import APIRouter, Request, Response, status
from pymilvus import MilvusClient  # type: ignore[import-untyped]

from app.api.schemas.system import HealthResponse, ReadinessResponse
from app.api.version import API_VERSION
from app.config import Settings
from app.harness.snapshot import PostgresRunArchive

# 后续业务接口会分别注册到 sessions、runs、scenarios 等路由模块。
router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="检查服务进程是否存活",
)
async def get_health() -> HealthResponse:
    """返回应用进程状态，不检查 PostgreSQL 或 Milvus 连通性。"""
    return HealthResponse(
        status="ok",
        service="opsmind",
        version=API_VERSION,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="检查运行依赖是否就绪",
)
async def get_readiness(request: Request, response: Response) -> ReadinessResponse:
    """检查配置中的归档数据库和 Milvus，不暴露连接信息或原始异常。"""
    settings = cast(Settings, request.app.state.settings)
    postgres = await _postgres_status(request)
    milvus = await _milvus_status(str(settings.milvus_url))
    readiness = ReadinessResponse(
        status="ready" if postgres != "unavailable" and milvus == "ok" else "not_ready",
        postgres=postgres,
        milvus=milvus,
    )
    if readiness.status == "not_ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return readiness


async def _postgres_status(request: Request) -> Literal["ok", "not_configured", "unavailable"]:
    """仅在 PostgreSQL 归档启用时检查数据库连接。"""
    archive = request.app.state.run_archive
    if not isinstance(archive, PostgresRunArchive):
        return "not_configured"
    try:
        await archive.ping()
    except Exception:  # 数据库驱动异常不应泄露给就绪接口调用方。
        return "unavailable"
    return "ok"


async def _milvus_status(milvus_url: str) -> Literal["ok", "unavailable"]:
    """在线程中执行同步 Milvus SDK 探测，避免阻塞 ASGI 事件循环。"""
    try:
        await asyncio.to_thread(_list_milvus_collections, milvus_url)
    except Exception:  # SDK 与网络异常均映射为固定安全状态。
        return "unavailable"
    return "ok"


def _list_milvus_collections(milvus_url: str) -> None:
    """建立短连接并执行只读集合列表查询。"""
    client = MilvusClient(uri=milvus_url)
    try:
        client.list_collections()
    finally:
        client.close()
