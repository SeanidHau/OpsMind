"""桌面端管理内置 MCP 观测连接的本机 API。"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from app.api.schemas.mcp import (
    McpConfigurationResponse,
    McpConfigurationUpdate,
    McpServiceResponse,
)
from app.mcp.configuration import McpConfiguration, McpConfigurationStore, McpServiceConfiguration

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


def response_from_configuration(configuration: McpConfiguration) -> McpConfigurationResponse:
    """投影可显示信息，避免凭据离开本机后端进程。"""

    def service_response(service: McpServiceConfiguration) -> McpServiceResponse:
        return McpServiceResponse(
            url=str(service.url) if service.url is not None else None,
            token_configured=service.bearer_token is not None,
        )

    return McpConfigurationResponse(
        enabled=configuration.enabled,
        command=configuration.command,
        arguments=configuration.arguments,
        prometheus=service_response(configuration.prometheus),
        loki=service_response(configuration.loki),
        jaeger=service_response(configuration.jaeger),
        kubernetes=service_response(configuration.kubernetes),
        cmdb=service_response(configuration.cmdb),
    )


def updated_service(
    current: McpServiceConfiguration, *, url: str, token: str
) -> McpServiceConfiguration:
    """空字段保留已有值，避免 UI 因不回显敏感信息而误覆盖配置。"""
    normalized_url = url.strip()
    return McpServiceConfiguration.model_validate(
        {
            "url": normalized_url or current.url,
            "bearer_token": token.strip() or current.bearer_token,
        }
    )


@router.get("", response_model=McpConfigurationResponse, summary="查看本机 MCP 连接配置")
async def get_mcp_configuration(request: Request) -> McpConfigurationResponse:
    """返回可编辑的连接信息和令牌存在状态。"""
    configuration = cast(McpConfiguration, request.app.state.mcp_configuration)
    return response_from_configuration(configuration)


@router.put("", response_model=McpConfigurationResponse, summary="保存并应用本机 MCP 连接配置")
async def update_mcp_configuration(
    payload: McpConfigurationUpdate, request: Request
) -> McpConfigurationResponse:
    """保存配置并重新装配受控工具目录；活跃运行不受影响。"""
    current = cast(McpConfiguration, request.app.state.mcp_configuration)
    try:
        configuration = McpConfiguration(
            enabled=payload.enabled,
            command=payload.command.strip(),
            arguments=payload.arguments.strip(),
            prometheus=updated_service(
                current.prometheus,
                url=payload.prometheus_url,
                token=payload.prometheus_bearer_token,
            ),
            loki=updated_service(
                current.loki, url=payload.loki_url, token=payload.loki_bearer_token
            ),
            jaeger=updated_service(
                current.jaeger, url=payload.jaeger_url, token=payload.jaeger_bearer_token
            ),
            kubernetes=updated_service(
                current.kubernetes,
                url=payload.kubernetes_url,
                token=payload.kubernetes_bearer_token,
            ),
            cmdb=updated_service(
                current.cmdb, url=payload.cmdb_url, token=payload.cmdb_bearer_token
            ),
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="MCP 配置无效"
        ) from error

    reconfigure = getattr(request.app.state, "reconfigure_mcp", None)
    if not callable(reconfigure):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="当前运行方式不支持更新 MCP 配置"
        )
    try:
        reconfigure(configuration)
        cast(McpConfigurationStore, request.app.state.mcp_configuration_store).save(configuration)
    except OSError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="无法保存 MCP 配置"
        ) from error
    return response_from_configuration(configuration)
