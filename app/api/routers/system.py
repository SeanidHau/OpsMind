"""系统级路由：不依赖外部基础设施。"""

from fastapi import APIRouter, status

from app.api.schemas.system import HealthResponse
from app.api.version import API_VERSION

# 后续业务接口会分别注册到 sessions、runs、scenarios 等路由模块。
router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="检查服务进程是否存活",
)
async def get_health() -> HealthResponse:
    """返回应用进程状态，不检查 PostgreSQL 或 Qdrant 连通性。"""
    return HealthResponse(
        status="ok",
        service="opsmind",
        version=API_VERSION,
    )
