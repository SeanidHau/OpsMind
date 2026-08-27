"""FastAPI 路由使用的显式应用依赖。"""

from typing import cast

from fastapi import HTTPException, Request, status

from app.diagnosis.runner import (
    ApprovedDiagnosisRunResumer,
    DiagnosisApprovalResolver,
    DiagnosisRunner,
    DiagnosisRunReader,
    DiagnosisRunResumer,
)
from app.tools.registry import ToolRegistry
from app.tools.scenarios import ScenarioStore


def get_scenario_store(request: Request) -> ScenarioStore:
    """从应用状态读取由应用工厂注入的场景目录。"""
    # 应用工厂始终初始化该字段；cast 仅帮助静态类型检查。
    return cast(ScenarioStore, request.app.state.scenario_store)


def get_tool_registry(request: Request) -> ToolRegistry:
    """从应用状态读取与场景存储绑定的只读工具注册表。"""
    # 诊断运行路由将通过该依赖取得可执行工具，不自行创建注册表。
    return cast(ToolRegistry, request.app.state.tool_registry)


def get_diagnosis_runner(request: Request) -> DiagnosisRunner:
    """返回应用装配的诊断运行器，未配置时拒绝创建运行。"""
    runner = getattr(request.app.state, "diagnosis_runner", None)
    if runner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="diagnosis runtime is not configured",
        )
    return cast(DiagnosisRunner, runner)


def get_diagnosis_run_reader(request: Request) -> DiagnosisRunReader:
    """返回运行查询接口；未配置快照读取能力时拒绝查询。"""
    runner = getattr(request.app.state, "diagnosis_runner", None)
    if runner is None or not callable(getattr(runner, "get_run", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="diagnosis run reader is not configured",
        )
    return cast(DiagnosisRunReader, runner)


def get_diagnosis_run_resumer(request: Request) -> DiagnosisRunResumer:
    """返回用户输入续跑接口；未配置时拒绝恢复运行。"""
    runner = getattr(request.app.state, "diagnosis_runner", None)
    if runner is None or not callable(getattr(runner, "resume_with_user_input", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="diagnosis run resumer is not configured",
        )
    return cast(DiagnosisRunResumer, runner)


def get_diagnosis_approval_resolver(request: Request) -> DiagnosisApprovalResolver:
    """返回审批决议接口；未配置时拒绝记录审批。"""
    runner = getattr(request.app.state, "diagnosis_runner", None)
    if runner is None or not callable(getattr(runner, "resolve_approval", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="diagnosis approval resolver is not configured",
        )
    return cast(DiagnosisApprovalResolver, runner)


def get_approved_diagnosis_run_resumer(request: Request) -> ApprovedDiagnosisRunResumer:
    """返回获批续跑接口；未配置时拒绝执行获批动作。"""
    runner = getattr(request.app.state, "diagnosis_runner", None)
    if runner is None or not callable(getattr(runner, "resume_approved", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="approved diagnosis run resumer is not configured",
        )
    return cast(ApprovedDiagnosisRunResumer, runner)
