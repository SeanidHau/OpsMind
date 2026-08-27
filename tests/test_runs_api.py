"""诊断运行 API 与应用运行器注入的验收测试。"""

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.harness.loop import create_initial_state
from app.models.contracts import BudgetState, DiagnosisState, HarnessStatus


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
