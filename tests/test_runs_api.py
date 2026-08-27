"""诊断运行 API 与应用运行器注入的验收测试。"""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.harness.loop import create_initial_state
from app.models.contracts import (
    BudgetState,
    DiagnosisState,
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
