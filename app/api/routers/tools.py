"""已注册诊断工具的只读目录 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_tool_registry
from app.api.schemas.tools import ToolSummary
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/api/v1", tags=["tools"])


@router.get(
    "/tools",
    response_model=list[ToolSummary],
    status_code=status.HTTP_200_OK,
    summary="列出当前应用可用的诊断工具",
)
async def get_tools(
    tool_registry: Annotated[ToolRegistry, Depends(get_tool_registry)],
) -> list[ToolSummary]:
    """返回稳定排序的工具策略摘要，不执行工具或暴露处理函数。"""
    return [
        ToolSummary(
            name=policy.name,
            risk_level=policy.risk_level,
            read_only=policy.read_only,
            requires_approval=policy.requires_approval,
            required_args=sorted(policy.required_args),
            allowed_args=(None if policy.allowed_args is None else sorted(policy.allowed_args)),
            max_calls_per_run=policy.max_calls_per_run,
        )
        for policy in sorted(tool_registry.policies(), key=lambda policy: policy.name)
    ]
