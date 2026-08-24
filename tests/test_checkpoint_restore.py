"""checkpoint 强类型状态恢复的验收测试。"""

from uuid import UUID

import pytest

from app.harness.loop import HarnessLoop, create_initial_state
from app.harness.policy import ActionPolicy
from app.harness.restore import RunStateRestorer
from app.harness.snapshot import InMemoryRunArchive, RunSnapshotFactory
from app.models.contracts import (
    ActionType,
    AgentAction,
    AgentEvent,
    BudgetConsumption,
    BudgetState,
    ContextItem,
    ContextSnapshot,
    ContextSource,
    DiagnosisReport,
    EventType,
    EvidenceItem,
    HarnessStatus,
    PlanItem,
    PlanRevision,
    PolicyDecision,
    PolicyOutcome,
    ProgressAssessment,
    ProgressStatus,
    RunSnapshot,
)


def populated_snapshot() -> RunSnapshot:
    """构造包含关键 Pydantic 状态对象的 JSON 化快照。"""
    state = create_initial_state(
        session_id="session-restore",
        thread_id="thread-restore",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )
    evidence = EvidenceItem(
        evidence_id="a" * 64,
        tool_name="query_metrics",
        content='{"error_rate":0.12}',
    )
    state.update(
        {
            "plan": [PlanItem(title="查询指标", rationale="验证错误率")],
            "plan_history": [
                PlanRevision(
                    version=1,
                    reason="建立初始诊断计划。",
                    items=[PlanItem(title="查询指标", rationale="验证错误率")],
                )
            ],
            "evidence": [evidence],
            "current_action": AgentAction(
                action_type=ActionType.CALL_TOOL,
                intent="查询支付服务指标",
                tool_name="query_metrics",
                reason="收集证据",
            ),
            "policy_decision": PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                reason="允许只读工具调用",
                consumption=BudgetConsumption(steps=1, tool_calls=1),
            ),
            "progress_assessment": ProgressAssessment(
                status=ProgressStatus.PROGRESSED,
                reason="发现新的错误率证据",
                fingerprint="metrics:payment",
                consecutive_stalls=0,
            ),
            "model_context": ContextSnapshot(
                items=[
                    ContextItem(
                        source=ContextSource.TASK,
                        reference="user_query",
                        content="支付服务请求超时",
                        priority=100,
                    )
                ],
                total_chars=8,
                truncated=False,
            ),
            "diagnosis_report": DiagnosisReport(
                summary="支付服务延迟升高。",
                probable_root_cause="数据库连接池耗尽。",
                confidence=0.8,
                evidence_ids=[evidence.evidence_id],
                recommended_actions=["检查连接池上限。"],
            ),
            "progress_status": ProgressStatus.PROGRESSED,
            "terminal_status": HarnessStatus.WAITING_APPROVAL,
            "trajectory": [
                AgentEvent(
                    run_id=UUID(state["run_id"]),
                    step_id=1,
                    event_type=EventType.RUN_PAUSED,
                )
            ],
        }
    )
    return RunSnapshotFactory().build(state)


def test_restorer_rebuilds_key_contract_types() -> None:
    """恢复后的状态应可直接供后续 Harness 节点读取。"""
    restored = RunStateRestorer().restore(populated_snapshot())

    assert isinstance(restored["budget"], BudgetState)
    assert isinstance(restored["plan"][0], PlanItem)
    assert isinstance(restored["plan_history"][0], PlanRevision)
    assert isinstance(restored["evidence"][0], EvidenceItem)
    assert isinstance(restored["current_action"], AgentAction)
    assert isinstance(restored["policy_decision"], PolicyDecision)
    assert isinstance(restored["progress_assessment"], ProgressAssessment)
    assert isinstance(restored["model_context"], ContextSnapshot)
    assert isinstance(restored["diagnosis_report"], DiagnosisReport)
    assert restored["progress_status"] is ProgressStatus.PROGRESSED
    assert restored["terminal_status"] is HarnessStatus.WAITING_APPROVAL


def test_restorer_isolates_restored_state_from_snapshot() -> None:
    """修改恢复状态不能污染归档中的 JSON 快照。"""
    snapshot = populated_snapshot()
    restored = RunStateRestorer().restore(snapshot)
    restored["evidence"][0].content = "已被恢复调用方修改"

    assert snapshot.final_state["evidence"][0]["content"] == '{"error_rate":0.12}'


def test_restorer_rejects_snapshot_missing_required_state() -> None:
    """缺少后续节点依赖的必填状态时，恢复必须明确失败。"""
    snapshot = populated_snapshot()
    snapshot.final_state.pop("budget")

    with pytest.raises(ValueError, match="snapshot cannot be restored"):
        RunStateRestorer().restore(snapshot)


def test_restorer_accepts_checkpoint_created_before_plan_history() -> None:
    """第 29 阶段之前的 checkpoint 缺少计划历史时仍可恢复。"""
    snapshot = populated_snapshot()
    snapshot.final_state.pop("plan_history")

    restored = RunStateRestorer().restore(snapshot)

    assert restored["plan_history"] == []


class UnusedActionProvider:
    """restore_checkpoint 不应触发模型调用。"""

    async def propose_action(self, state: object) -> AgentAction:
        """若被调用则说明恢复错误地启动了图。"""
        del state
        raise AssertionError("restore_checkpoint must not call the action provider")


class UnusedToolExecutor:
    """restore_checkpoint 不应触发工具调用。"""

    async def execute(self, action: AgentAction) -> dict[str, object]:
        """若被调用则说明恢复错误地启动了图。"""
        del action
        raise AssertionError("restore_checkpoint must not call the tool executor")


def test_loop_restores_archived_checkpoint_without_running_graph() -> None:
    """Loop 入口应只读取归档并返回强类型状态。"""
    snapshot = populated_snapshot()
    archive = InMemoryRunArchive()
    archive.save(snapshot)
    loop = HarnessLoop(
        action_provider=UnusedActionProvider(),
        tool_executor=UnusedToolExecutor(),
        policy=ActionPolicy([]),
        run_archive=archive,
    )

    restored = loop.restore_checkpoint(snapshot.run_id)

    assert restored["run_id"] == str(snapshot.run_id)
    assert restored["trajectory"][0].event_type is EventType.RUN_PAUSED
