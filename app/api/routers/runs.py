"""诊断运行的 HTTP 入口。"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import suppress
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    get_approved_diagnosis_run_resumer,
    get_diagnosis_approval_resolver,
    get_diagnosis_run_reader,
    get_diagnosis_run_resumer,
    get_diagnosis_runner,
    get_streaming_diagnosis_runner,
)
from app.api.schemas.runs import (
    CreateDiagnosisRunRequest,
    DiagnosisRunResponse,
    DiagnosisRunTrajectoryResponse,
    DiagnosisTrajectoryEventResponse,
    ResumeDiagnosisRunRequest,
)
from app.api.streaming import QueueEventObserver
from app.diagnosis.runner import (
    ApprovedDiagnosisRunResumer,
    DiagnosisApprovalResolver,
    DiagnosisRunner,
    DiagnosisRunReader,
    DiagnosisRunResumer,
    StreamingDiagnosisRunner,
)
from app.models.contracts import AgentEvent, ApprovalCommand, DiagnosisState, ReplayResult

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


def trajectory_event_response(event: AgentEvent) -> DiagnosisTrajectoryEventResponse:
    """投影审计事件，排除工具参数、工具观察结果和模型上下文。"""
    return DiagnosisTrajectoryEventResponse(
        event_id=event.event_id,
        step_id=event.step_id,
        event_type=event.event_type,
        timestamp=event.timestamp,
        node=event.node,
        action_type=event.action.action_type if event.action is not None else None,
        tool_name=event.action.tool_name if event.action is not None else None,
        latency_ms=event.latency_ms,
        token_usage=event.token_usage,
        decision=event.decision,
        error=event.error,
    )


def trajectory_response(replay: ReplayResult) -> DiagnosisRunTrajectoryResponse:
    """将缓存回放转换为可公开查询的安全轨迹。"""
    events = [trajectory_event_response(event) for event in replay.trajectory]
    return DiagnosisRunTrajectoryResponse(
        run_id=replay.source_run_id,
        mode=replay.mode,
        status=replay.terminal_status,
        event_count=len(events),
        events=events,
    )


def sse_event(*, event: str, data: Mapping[str, Any]) -> str:
    """编码一条 Server-Sent Event，供浏览器按事件类型消费。"""
    return f"event: {event}\\ndata: {json.dumps(data, ensure_ascii=False)}\\n\\n"


def trajectory_sse_stream(replay: ReplayResult) -> Iterator[str]:
    """按归档顺序输出安全事件，最后写入流完成事件。"""
    for event in replay.trajectory:
        payload = trajectory_event_response(event).model_dump(mode="json")
        yield sse_event(event="trajectory_event", data=payload)

    yield sse_event(
        event="stream_completed",
        data={
            "run_id": str(replay.source_run_id),
            "event_count": len(replay.trajectory),
            "status": replay.terminal_status,
        },
    )


async def live_run_sse_stream(
    *,
    payload: CreateDiagnosisRunRequest,
    run_id: UUID,
    diagnosis_runner: StreamingDiagnosisRunner,
) -> AsyncIterator[str]:
    """运行诊断并在节点提交后持续输出安全事件。"""
    event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    event_observer = QueueEventObserver(event_queue)

    async def run_diagnosis() -> DiagnosisState:
        try:
            return await diagnosis_runner.run_with_event_observer(
                session_id=payload.session_id,
                thread_id=payload.thread_id,
                user_query=payload.user_query,
                run_id=run_id,
                event_observer=event_observer,
            )
        finally:
            event_queue.put_nowait(None)

    run_task = asyncio.create_task(run_diagnosis())
    yield sse_event(event="run_started", data={"run_id": str(run_id)})

    try:
        while True:
            event = await event_queue.get()
            if event is None:
                break
            yield sse_event(
                event=event.event_type.value,
                data=trajectory_event_response(event).model_dump(mode="json"),
            )

        result = await run_task
    except asyncio.CancelledError:
        raise
    except Exception:
        yield sse_event(
            event="run_failed",
            data={"run_id": str(run_id), "error": "diagnosis run failed"},
        )
    else:
        yield sse_event(
            event="run_finished",
            data=response_from_state(result).model_dump(mode="json"),
        )
    finally:
        if not run_task.done():
            run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


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
    "/runs/stream",
    status_code=status.HTTP_200_OK,
    summary="创建诊断运行并以 SSE 返回执行中事件",
)
async def stream_diagnosis_run(
    payload: CreateDiagnosisRunRequest,
    diagnosis_runner: Annotated[
        StreamingDiagnosisRunner,
        Depends(get_streaming_diagnosis_runner),
    ],
) -> StreamingResponse:
    """建立请求专属事件流，运行完成或失败后关闭连接。"""
    return StreamingResponse(
        live_run_sse_stream(
            payload=payload,
            run_id=uuid4(),
            diagnosis_runner=diagnosis_runner,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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


@router.post(
    "/runs/{run_id}/approval",
    response_model=DiagnosisRunResponse,
    status_code=status.HTTP_200_OK,
    summary="记录高风险动作的审批决议",
)
async def resolve_diagnosis_approval(
    run_id: UUID,
    command: ApprovalCommand,
    approval_resolver: Annotated[
        DiagnosisApprovalResolver,
        Depends(get_diagnosis_approval_resolver),
    ],
) -> DiagnosisRunResponse:
    """保存批准、编辑或拒绝决议，但不在当前请求执行工具。"""
    try:
        result = approval_resolver.resolve_approval(run_id=run_id, command=command)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run cannot accept approval",
        ) from error

    return response_from_state(result)


@router.post(
    "/runs/{run_id}/approval/resume",
    response_model=DiagnosisRunResponse,
    status_code=status.HTTP_200_OK,
    summary="续跑已批准的高风险动作",
)
async def resume_approved_diagnosis_run(
    run_id: UUID,
    approved_run_resumer: Annotated[
        ApprovedDiagnosisRunResumer,
        Depends(get_approved_diagnosis_run_resumer),
    ],
) -> DiagnosisRunResponse:
    """从审批决议 checkpoint 恢复，不重新提交审批。"""
    try:
        result = await approved_run_resumer.resume_approved(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run cannot resume approved action",
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


@router.get(
    "/runs/{run_id}/trajectory",
    response_model=DiagnosisRunTrajectoryResponse,
    status_code=status.HTTP_200_OK,
    summary="读取已归档诊断运行的安全轨迹",
)
async def get_diagnosis_run_trajectory(
    run_id: UUID,
    diagnosis_run_reader: Annotated[DiagnosisRunReader, Depends(get_diagnosis_run_reader)],
) -> DiagnosisRunTrajectoryResponse:
    """返回缓存时间线，不重新执行模型、工具或 Harness 节点。"""
    try:
        replay = diagnosis_run_reader.get_run(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        ) from error

    return trajectory_response(replay)


@router.post(
    "/runs/{run_id}/replay",
    response_model=DiagnosisRunTrajectoryResponse,
    status_code=status.HTTP_200_OK,
    summary="回放已归档诊断运行",
)
async def replay_diagnosis_run(
    run_id: UUID,
    diagnosis_run_reader: Annotated[DiagnosisRunReader, Depends(get_diagnosis_run_reader)],
) -> DiagnosisRunTrajectoryResponse:
    """返回一份独立的安全轨迹副本，不重新运行诊断。"""
    try:
        replay = diagnosis_run_reader.get_run(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        ) from error

    return trajectory_response(replay)


@router.get(
    "/runs/{run_id}/events",
    status_code=status.HTTP_200_OK,
    summary="以 SSE 回放已归档诊断运行事件",
)
async def stream_diagnosis_run_events(
    run_id: UUID,
    diagnosis_run_reader: Annotated[DiagnosisRunReader, Depends(get_diagnosis_run_reader)],
) -> StreamingResponse:
    """流式返回缓存的安全轨迹，不重新执行模型、工具或 Harness 节点。"""
    try:
        replay = diagnosis_run_reader.get_run(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        ) from error

    return StreamingResponse(
        trajectory_sse_stream(replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
