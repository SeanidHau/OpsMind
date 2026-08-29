"""应用工厂装配 Harness 诊断运行链的验收测试。"""

from collections import deque
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.diagnosis.runner import HarnessDiagnosisRunner
from app.diagnosis.runtime import default_budget_template
from app.harness.evidence import EvidenceCollector
from app.models.contracts import (
    ActionType,
    AgentAction,
    DiagnosisReport,
    FusedRetrievalHit,
    IncidentScenario,
    KnowledgeChunk,
    ScenarioLog,
)
from app.tools.scenarios import ScenarioStore


class QueueActionProvider:
    """按固定顺序提供动作，模拟一个已配置的结构化模型。"""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = deque(actions)

    async def propose_action(self, state: dict[str, Any]) -> AgentAction:
        """返回下一个动作，避免测试连接真实模型供应商。"""
        del state
        return self._actions.popleft()


class FakeKnowledgeSearcher:
    """返回固定 Runbook 命中的检索替身。"""

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[FusedRetrievalHit]:
        del query, top_k, metadata_filter
        return [
            FusedRetrievalHit(
                chunk=KnowledgeChunk(
                    chunk_id="payment-db",
                    source_id="payment-connection-pool-runbook",
                    index=0,
                    content="连接池耗尽会导致支付超时。",
                    metadata={"service": "payment-service"},
                ),
                score=0.03,
                rank=1,
                retriever_names=["bm25", "vector"],
            )
        ]

    def close(self) -> None:
        pass


def test_default_budget_covers_a_complete_standard_diagnosis() -> None:
    """默认预算应容纳规划、取证和汇总，其他限制仍由 Harness 保留。"""
    budget = default_budget_template()

    assert budget.max_model_calls == 8
    assert budget.max_tokens == 24_000


def make_scenario_store() -> ScenarioStore:
    """构造包含固定指标证据的支付服务场景。"""
    return ScenarioStore(
        [
            IncidentScenario(
                scenario_id="payment-timeout",
                service="payment-service",
                logs=[
                    ScenarioLog(
                        timestamp="2026-08-28T10:00:00Z",
                        level="ERROR",
                        message="database connection pool exhausted",
                    )
                ],
                metrics={"error_rate": 0.12},
                dependencies=["postgres-primary"],
            )
        ]
    )


def completed_actions() -> list[AgentAction]:
    """构造一次指标查询后输出带证据报告的动作序列。"""
    observation = {"service": "payment-service", "metrics": {"error_rate": 0.12}}
    evidence = EvidenceCollector().collect(tool_name="query_metrics", observation=observation)
    report = DiagnosisReport(
        summary="支付服务错误率升高。",
        probable_root_cause="数据库连接池耗尽。",
        confidence=0.8,
        evidence_ids=[evidence.evidence_id],
        recommended_actions=["检查数据库连接池上限。"],
    )
    return [
        AgentAction(
            action_type=ActionType.CALL_TOOL,
            intent="查询支付服务指标",
            tool_name="query_metrics",
            tool_args={"service": "payment-service"},
            reason="收集诊断证据。",
        ),
        AgentAction(
            action_type=ActionType.FINAL_ANSWER,
            intent="输出诊断结论",
            reason="指标证据满足诊断要求。",
            report=report,
        ),
    ]


def knowledge_completed_actions() -> list[AgentAction]:
    """构造查询 Runbook 后引用该证据的动作序列。"""
    observation = {
        "query": "支付服务连接池耗尽",
        "count": 1,
        "hits": [
            {
                "chunk_id": "payment-db",
                "source_id": "payment-connection-pool-runbook",
                "content": "连接池耗尽会导致支付超时。",
                "metadata": {"service": "payment-service"},
                "score": 0.03,
                "retriever_names": ["bm25", "vector"],
            }
        ],
    }
    evidence = EvidenceCollector().collect(tool_name="query_knowledge", observation=observation)
    report = DiagnosisReport(
        summary="支付服务请求超时。",
        probable_root_cause="数据库连接池耗尽。",
        confidence=0.8,
        evidence_ids=[evidence.evidence_id],
        recommended_actions=["检查连接泄漏和慢查询。"],
    )
    return [
        AgentAction(
            action_type=ActionType.CALL_TOOL,
            intent="检索支付服务 Runbook",
            tool_name="query_knowledge",
            tool_args={"query": "支付服务连接池耗尽", "service": "payment-service"},
            reason="补充故障处理知识。",
        ),
        AgentAction(
            action_type=ActionType.FINAL_ANSWER,
            intent="输出诊断结论",
            reason="Runbook 证据满足诊断要求。",
            report=report,
        ),
    ]


def test_application_builds_harness_runner_from_action_provider() -> None:
    """注入动作提供器时，应用工厂必须装配真实 Harness 运行器。"""
    app = create_app(
        scenario_store=make_scenario_store(),
        action_provider=QueueActionProvider(completed_actions()),
    )

    assert isinstance(app.state.diagnosis_runner, HarnessDiagnosisRunner)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/runs",
            json={
                "session_id": "session-runtime",
                "thread_id": "thread-runtime",
                "user_query": "支付服务请求超时",
            },
        )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert response.json()["step_count"] == 2
    assert "# OpsMind 诊断报告" in response.json()["final_answer"]


def test_application_rejects_ambiguous_runner_configuration() -> None:
    """显式运行器和动作提供器不能同时注入，避免来源不明确。"""
    with pytest.raises(
        ValueError,
        match="diagnosis_runner and action_provider cannot be provided together",
    ):
        create_app(
            diagnosis_runner=object(),  # type: ignore[arg-type]
            action_provider=QueueActionProvider(completed_actions()),
        )


def test_application_renders_knowledge_sources_from_harness_evidence() -> None:
    """知识工具结果经 Harness 证据链后必须显示来源 Runbook。"""
    app = create_app(
        scenario_store=make_scenario_store(),
        action_provider=QueueActionProvider(knowledge_completed_actions()),
        knowledge_searcher=FakeKnowledgeSearcher(),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/runs",
            json={
                "session_id": "session-rag-runtime",
                "thread_id": "thread-rag-runtime",
                "user_query": "支付服务请求超时",
            },
        )

    assert response.status_code == 201
    assert "## 知识来源" in response.json()["final_answer"]
    assert "`payment-connection-pool-runbook`" in response.json()["final_answer"]
