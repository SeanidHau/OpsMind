"""ProgressVerifier 的验收测试。"""

from typing import Any

from app.harness.progress import ProgressVerifier
from app.models.contracts import ActionType, AgentAction, ProgressStatus
from tests.support import diagnosis_report


def tool_action() -> AgentAction:
    """构造固定参数的工具调用动作。"""
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        intent="查询支付服务指标",
        tool_name="query_metrics",
        tool_args={"service": "payment"},
        reason="收集性能证据",
    )


def observation(value: int) -> dict[str, Any]:
    """构造可用于指纹比较的工具观察结果。"""
    return {"tool_name": "query_metrics", "result": {"error_rate": value}}


def test_new_observation_is_progress() -> None:
    """首次出现的动作和观察组合应视为有进展。"""
    assessment = ProgressVerifier().assess(
        action=tool_action(),
        observation=observation(5),
        previous_fingerprints=[],
        consecutive_stalls=0,
    )

    assert assessment.status is ProgressStatus.PROGRESSED
    assert assessment.fingerprint is not None
    assert assessment.consecutive_stalls == 0


def test_same_action_with_new_observation_is_progress() -> None:
    """相同工具返回不同结果时仍有新证据，不能误判为循环。"""
    verifier = ProgressVerifier()
    first = verifier.assess(
        action=tool_action(),
        observation=observation(5),
        previous_fingerprints=[],
        consecutive_stalls=0,
    )
    second = verifier.assess(
        action=tool_action(),
        observation=observation(7),
        previous_fingerprints=[first.fingerprint or ""],
        consecutive_stalls=0,
    )

    assert second.status is ProgressStatus.PROGRESSED


def test_repeated_observation_replans_then_stops() -> None:
    """连续两次停滞建议重规划，连续三次停滞建议强制终止。"""
    verifier = ProgressVerifier()
    first = verifier.assess(
        action=tool_action(),
        observation=observation(5),
        previous_fingerprints=[],
        consecutive_stalls=0,
    )
    history = [first.fingerprint or ""]

    stalled_once = verifier.assess(
        action=tool_action(),
        observation=observation(5),
        previous_fingerprints=history,
        consecutive_stalls=0,
    )
    stalled_twice = verifier.assess(
        action=tool_action(),
        observation=observation(5),
        previous_fingerprints=history,
        consecutive_stalls=stalled_once.consecutive_stalls,
    )
    stalled_three_times = verifier.assess(
        action=tool_action(),
        observation=observation(5),
        previous_fingerprints=history,
        consecutive_stalls=stalled_twice.consecutive_stalls,
    )

    assert stalled_once.should_replan is False
    assert stalled_twice.should_replan is True
    assert stalled_twice.should_stop is False
    assert stalled_three_times.should_stop is True


def test_final_answer_completes_progress() -> None:
    """最终回答动作应显式标记当前任务完成。"""
    action = AgentAction(
        action_type=ActionType.FINAL_ANSWER,
        intent="输出诊断结果",
        reason="诊断完成",
        report=diagnosis_report(),
    )

    assessment = ProgressVerifier().assess(
        action=action,
        observation=None,
        previous_fingerprints=[],
        consecutive_stalls=2,
    )

    assert assessment.status is ProgressStatus.COMPLETED
    assert assessment.consecutive_stalls == 0
