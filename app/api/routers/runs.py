"""诊断运行的同步 HTTP 入口。"""

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    get_diagnosis_run_reader,
    get_diagnosis_run_resumer,
    get_diagnosis_runner,
)
from app.api.schemas.runs import (
    CreateDiagnosisRunRequest,
    DiagnosisRunResponse,
    ResumeDiagnosisRunRequest,
)
from app.diagnosis.runner import DiagnosisRunner, DiagnosisRunReader, DiagnosisRunResumer

router = APIRouter(prefix="/api/v1", tags=["runs"])


def response_from_state(result: Mapping[str, Any]) -> DiagnosisRunResponse:
    """将 Harness 状态投影为不会泄露内部上下文的 HTTP 摘要。"""
    return DiagnosisRunResponse(
        run_id=UUID(str(result["run_id"])),
        status=result.get("terminal_status"),
        step_count=int(result["step_count"]),
        final_answer=result.get("final_answer"),
        pending_question=result.get("pending_question"),
        errors=list(result["errors"]),
    )


@router.post(
    "/runs",
    response_model=DiagnosisRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建一次受控诊断运行",
)
async def create_diagnosis_run(
    payload: CreateDiagnosisRunRequest,
    diagnosis_runner: Annotated[DiagnosisRunner, Depends(get_diagnosis_runner)],
) -> DiagnosisRunResponse:
    """委托已注入的运行器，不在路由层创建模型或 Harness。"""
    result = await diagnosis_runner.run(
        session_id=payload.session_id,
        thread_id=payload.thread_id,
        user_query=payload.user_query,
    )
    return response_from_state(result)


@router.post(
    "/runs/{run_id}/input",
    response_model=DiagnosisRunResponse,
    status_code=status.HTTP_200_OK,
    summary="提交用户回答并续跑诊断",
)
async def resume_diagnosis_run_with_user_input(
    run_id: UUID,
    payload: ResumeDiagnosisRunRequest,
    diagnosis_run_resumer: Annotated[DiagnosisRunResumer, Depends(get_diagnosis_run_resumer)],
) -> DiagnosisRunResponse:
    """仅恢复等待用户输入的快照，不创建新的诊断运行。"""
    try:
        result = await diagnosis_run_resumer.resume_with_user_input(run_id, payload.answer)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run cannot accept user input",
        ) from error

    return response_from_state(result)


@router.get(
    "/runs/{run_id}",
    response_model=DiagnosisRunResponse,
    status_code=status.HTTP_200_OK,
    summary="读取已归档诊断运行",
)
async def get_diagnosis_run(
    run_id: UUID,
    diagnosis_run_reader: Annotated[DiagnosisRunReader, Depends(get_diagnosis_run_reader)],
) -> DiagnosisRunResponse:
    """只读取归档快照，不重新执行模型、工具或 Harness。"""
    try:
        replay = diagnosis_run_reader.get_run(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        ) from error

    final_state = replay.final_state
    return DiagnosisRunResponse(
        run_id=replay.source_run_id,
        status=replay.terminal_status,
        step_count=int(final_state["step_count"]),
        final_answer=final_state.get("final_answer"),
        pending_question=final_state.get("pending_question"),
        errors=list(final_state["errors"]),
    )
