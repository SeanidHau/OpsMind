"""诊断运行 API 与应用运行器注入的验收测试。"""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.harness.loop import create_initial_state
from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    ApprovalCommand,
    ApprovalDecision,
    BudgetState,
    DiagnosisState,
    EventType,
    HarnessStatus,
    ReplayMode,
    ReplayResult,
)


class RecordingDiagnosisRunner:
    """返回固定完成结果，并记录路由传入的任务信息。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def run(
        self,
        *,
        session_id: str,
        thread_id: str,
        user_query: str,
    ) -> DiagnosisState:
        """记录输入并构造最小完成状态。"""
        self.calls.append(
            {
                "session_id": session_id,
                "thread_id": thread_id,
                "user_query": user_query,
            }
        )
        state = create_initial_state(
            session_id=session_id,
            thread_id=thread_id,
            user_query=user_query,
            budget=BudgetState(
                max_steps=4,
                max_tool_calls=2,
                max_model_calls=2,
                max_tokens=1_000,
                max_runtime_seconds=60,
                max_estimated_cost_usd=0.1,
            ),
        )
        state["terminal_status"] = HarnessStatus.COMPLETED
        state["step_count"] = 2
        state["final_answer"] = "诊断已完成。"
        return state


class ReplayDiagnosisRunner(RecordingDiagnosisRunner):
    """在固定运行器上增加只读快照查询能力。"""

    def __init__(self, replay: ReplayResult) -> None:
        super().__init__()
        self.replay = replay
        self.read_run_ids: list[UUID] = []

    def get_run(self, run_id: UUID) -> ReplayResult:
        """返回匹配的缓存结果；未知 ID 按归档契约抛出 KeyError。"""
        self.read_run_ids.append(run_id)
        if run_id != self.replay.source_run_id:
            raise KeyError(run_id)
        return self.replay


class ResumableDiagnosisRunner(RecordingDiagnosisRunner):
    """记录用户回答，并返回固定的续跑完成状态。"""

    def __init__(self) -> None:
        super().__init__()
        self.resume_calls: list[tuple[UUID, str]] = []

    async def resume_with_user_input(self, run_id: UUID, answer: str) -> DiagnosisState:
        """模拟恢复等待用户输入的同一运行。"""
        self.resume_calls.append((run_id, answer))
        state = await self.run(
            session_id="session-resume",
            thread_id="thread-resume",
            user_query="恢复诊断",
        )
        state["run_id"] = str(run_id)
        state["terminal_status"] = HarnessStatus.COMPLETED
        state["step_count"] = 3
        state["final_answer"] = "已根据补充信息完成诊断。"
        return state


class ApprovalDiagnosisRunner(RecordingDiagnosisRunner):
    """记录审批决议与获批续跑，验证两步调用边界。"""

    def __init__(self) -> None:
        super().__init__()
        self.approval_calls: list[tuple[UUID, ApprovalCommand]] = []
        self.approved_resume_calls: list[UUID] = []

    def resolve_approval(self, *, run_id: UUID, command: ApprovalCommand) -> DiagnosisState:
        """模拟仅保存审批决议，不执行工具。"""
        self.approval_calls.append((run_id, command))
        state = create_initial_state(
            session_id="session-approval",
            thread_id="thread-approval",
            user_query="审批诊断",
            budget=BudgetState(
                max_steps=4,
                max_tool_calls=2,
                max_model_calls=2,
                max_tokens=1_000,
                max_runtime_seconds=60,
                max_estimated_cost_usd=0.1,
            ),
        )
        state["run_id"] = str(run_id)
        return state

    async def resume_approved(self, run_id: UUID) -> DiagnosisState:
        """模拟在独立调用中执行已批准动作后的结果。"""
        self.approved_resume_calls.append(run_id)
        state = await self.run(
            session_id="session-approval",
            thread_id="thread-approval",
            user_query="审批诊断",
        )
        state["run_id"] = str(run_id)
        state["step_count"] = 4
        state["final_answer"] = "获批动作已执行并完成诊断。"
        return state


def create_payload() -> dict[str, str]:
    """返回一个合法的创建运行请求。"""
    return {
        "session_id": "session-api-1",
        "thread_id": "thread-api-1",
        "user_query": "支付服务延迟升高",
    }


def test_runs_endpoint_returns_503_when_runtime_is_not_configured() -> None:
    """默认应用不应伪造模型运行结果。"""
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/runs", json=create_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "diagnosis runtime is not configured"}


def test_runs_endpoint_delegates_to_injected_diagnosis_runner() -> None:
    """路由将请求交给应用注入的运行器，并返回安全摘要。"""
    runner = RecordingDiagnosisRunner()

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.post("/api/v1/runs", json=create_payload())

    assert response.status_code == 201
    assert response.json() == {
        "run_id": response.json()["run_id"],
        "status": "completed",
        "step_count": 2,
        "final_answer": "诊断已完成。",
        "pending_question": None,
        "errors": [],
    }
    assert runner.calls == [create_payload()]


def test_run_query_endpoint_returns_cached_result_without_starting_a_run() -> None:
    """读取已归档运行只调用查询接口，不会调用运行器的 run 方法。"""
    run_id = uuid4()
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=run_id,
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.COMPLETED,
            final_state={
                "step_count": 3,
                "final_answer": "诊断已完成。",
                "pending_question": None,
                "errors": [],
            },
            trajectory=[],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": str(run_id),
        "status": "completed",
        "step_count": 3,
        "final_answer": "诊断已完成。",
        "pending_question": None,
        "errors": [],
    }
    assert runner.read_run_ids == [run_id]
    assert runner.calls == []


def test_run_query_endpoint_returns_not_found_for_unknown_run() -> None:
    """未知运行 ID 必须返回 404，不能伪造历史状态。"""
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=uuid4(),
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.BLOCKED,
            final_state={
                "step_count": 0,
                "final_answer": None,
                "pending_question": None,
                "errors": ["blocked"],
            },
            trajectory=[],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.get(f"/api/v1/runs/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


def test_run_trajectory_endpoint_returns_safe_cached_events() -> None:
    """轨迹查询仅读取快照，并且不返回工具参数或原始观察结果。"""
    run_id = uuid4()
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=run_id,
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.COMPLETED,
            final_state={
                "step_count": 1,
                "final_answer": "诊断已完成。",
                "pending_question": None,
                "errors": [],
            },
            trajectory=[
                AgentEvent(
                    run_id=run_id,
                    step_id=1,
                    event_type=EventType.TOOL_FINISHED,
                    node="execute_tool",
                    action=AgentAction(
                        action_type=ActionType.CALL_TOOL,
                        intent="读取服务状态。",
                        tool_name="get_service_status",
                        tool_args={"service": "payment", "api_key": "secret"},
                        reason="需要确认服务健康状态。",
                    ),
                    observation={"credentials": "secret", "status": "healthy"},
                    latency_ms=12,
                    token_usage={"total_tokens": 8},
                    decision="工具执行完成。",
                )
            ],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.get(f"/api/v1/runs/{run_id}/trajectory")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == str(run_id)
    assert payload["mode"] == "cached"
    assert payload["status"] == "completed"
    assert payload["event_count"] == 1
    assert payload["events"][0] == {
        "event_id": payload["events"][0]["event_id"],
        "step_id": 1,
        "event_type": "tool_finished",
        "timestamp": payload["events"][0]["timestamp"],
        "node": "execute_tool",
        "action_type": "call_tool",
        "tool_name": "get_service_status",
        "latency_ms": 12,
        "token_usage": {"total_tokens": 8},
        "decision": "工具执行完成。",
        "error": None,
    }
    assert "tool_args" not in payload["events"][0]
    assert "observation" not in payload["events"][0]
    assert runner.read_run_ids == [run_id]
    assert runner.calls == []


def test_run_trajectory_endpoint_returns_not_found_for_unknown_run() -> None:
    """未知运行的轨迹查询必须返回 404。"""
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=uuid4(),
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.BLOCKED,
            final_state={
                "step_count": 0,
                "final_answer": None,
                "pending_question": None,
                "errors": ["blocked"],
            },
            trajectory=[],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.get(f"/api/v1/runs/{uuid4()}/trajectory")

    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


def test_run_replay_endpoint_returns_cached_trajectory_without_starting_a_run() -> None:
    """显式回放只能读取快照，不得再次进入诊断运行。"""
    run_id = uuid4()
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=run_id,
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.COMPLETED,
            final_state={
                "step_count": 1,
                "final_answer": "诊断已完成。",
                "pending_question": None,
                "errors": [],
            },
            trajectory=[
                AgentEvent(
                    run_id=run_id,
                    step_id=1,
                    event_type=EventType.RUN_COMPLETED,
                    node="finalize",
                )
            ],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.post(f"/api/v1/runs/{run_id}/replay")

    assert response.status_code == 200
    assert response.json()["run_id"] == str(run_id)
    assert response.json()["mode"] == "cached"
    assert response.json()["event_count"] == 1
    assert response.json()["events"][0]["event_type"] == "run_completed"
    assert runner.read_run_ids == [run_id]
    assert runner.calls == []


def test_run_replay_endpoint_returns_not_found_for_unknown_run() -> None:
    """未知运行不能伪造回放结果。"""
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=uuid4(),
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.BLOCKED,
            final_state={
                "step_count": 0,
                "final_answer": None,
                "pending_question": None,
                "errors": ["blocked"],
            },
            trajectory=[],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.post(f"/api/v1/runs/{uuid4()}/replay")

    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


def test_run_events_endpoint_streams_safe_cached_events_as_sse() -> None:
    """SSE 回放按轨迹顺序发送安全事件和结束标记。"""
    run_id = uuid4()
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=run_id,
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.COMPLETED,
            final_state={
                "step_count": 1,
                "final_answer": "诊断已完成。",
                "pending_question": None,
                "errors": [],
            },
            trajectory=[
                AgentEvent(
                    run_id=run_id,
                    step_id=1,
                    event_type=EventType.TOOL_FINISHED,
                    node="execute_tool",
                    action=AgentAction(
                        action_type=ActionType.CALL_TOOL,
                        intent="读取服务状态。",
                        tool_name="get_service_status",
                        tool_args={"api_key": "secret"},
                        reason="需要确认服务健康状态。",
                    ),
                    observation={"credentials": "secret", "status": "healthy"},
                )
            ],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.get(f"/api/v1/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("event: trajectory_event") == 1
    assert response.text.count("event: stream_completed") == 1
    assert '"event_type": "tool_finished"' in response.text
    assert '"tool_name": "get_service_status"' in response.text
    assert '"event_count": 1' in response.text
    assert "api_key" not in response.text
    assert "credentials" not in response.text
    assert runner.read_run_ids == [run_id]
    assert runner.calls == []


def test_run_events_endpoint_returns_not_found_for_unknown_run() -> None:
    """未知运行的 SSE 查询必须在建立事件流前返回 404。"""
    runner = ReplayDiagnosisRunner(
        ReplayResult(
            source_run_id=uuid4(),
            mode=ReplayMode.CACHED,
            terminal_status=HarnessStatus.BLOCKED,
            final_state={
                "step_count": 0,
                "final_answer": None,
                "pending_question": None,
                "errors": ["blocked"],
            },
            trajectory=[],
        )
    )

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.get(f"/api/v1/runs/{uuid4()}/events")

    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


def test_run_input_endpoint_resumes_the_same_run() -> None:
    """用户回答必须交给续跑接口，并保持原始运行 ID。"""
    runner = ResumableDiagnosisRunner()
    run_id = uuid4()

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.post(
            f"/api/v1/runs/{run_id}/input",
            json={"answer": "  数据库连接数已达到上限  "},
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == str(run_id)
    assert response.json()["status"] == "completed"
    assert response.json()["final_answer"] == "已根据补充信息完成诊断。"
    assert runner.resume_calls == [(run_id, "数据库连接数已达到上限")]


def test_run_input_endpoint_rejects_blank_answer() -> None:
    """空白回答必须在写入运行历史前被 API 参数校验拒绝。"""
    with TestClient(create_app(diagnosis_runner=ResumableDiagnosisRunner())) as client:
        response = client.post(f"/api/v1/runs/{uuid4()}/input", json={"answer": "   "})

    assert response.status_code == 422


def test_run_input_endpoint_returns_service_unavailable_without_resumer() -> None:
    """仅支持新运行的注入对象不能被错误当作续跑器。"""
    with TestClient(create_app(diagnosis_runner=RecordingDiagnosisRunner())) as client:
        response = client.post(
            f"/api/v1/runs/{uuid4()}/input",
            json={"answer": "补充信息"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "diagnosis run resumer is not configured"}


def test_approval_endpoint_records_decision_without_resuming() -> None:
    """记录批准决议时，不得在同一请求执行获批动作。"""
    runner = ApprovalDiagnosisRunner()
    run_id = uuid4()

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.post(
            f"/api/v1/runs/{run_id}/approval",
            json={"decision": "approve", "reason": "维护窗口已确认。"},
        )

    assert response.status_code == 200
    assert runner.approval_calls == [
        (
            run_id,
            ApprovalCommand(decision=ApprovalDecision.APPROVE, reason="维护窗口已确认。"),
        )
    ]
    assert runner.approved_resume_calls == []


def test_approval_resume_endpoint_runs_only_after_explicit_request() -> None:
    """获批动作必须由单独的续跑请求触发。"""
    runner = ApprovalDiagnosisRunner()
    run_id = uuid4()

    with TestClient(create_app(diagnosis_runner=runner)) as client:
        response = client.post(f"/api/v1/runs/{run_id}/approval/resume")

    assert response.status_code == 200
    assert response.json()["run_id"] == str(run_id)
    assert response.json()["final_answer"] == "获批动作已执行并完成诊断。"
    assert runner.approval_calls == []
    assert runner.approved_resume_calls == [run_id]
