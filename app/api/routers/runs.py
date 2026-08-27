"""诊断运行的同步 HTTP 入口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_diagnosis_run_reader, get_diagnosis_runner
from app.api.schemas.runs import CreateDiagnosisRunRequest, DiagnosisRunResponse
from app.diagnosis.runner import DiagnosisRunner, DiagnosisRunReader

router = APIRouter(prefix="/api/v1", tags=["runs"])


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
    return DiagnosisRunResponse(
        run_id=UUID(result["run_id"]),
        status=result.get("terminal_status"),
        step_count=result["step_count"],
        final_answer=result.get("final_answer"),
        pending_question=result.get("pending_question"),
        errors=result["errors"],
    )


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
