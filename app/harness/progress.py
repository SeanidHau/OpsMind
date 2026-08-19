"""Harness Loop 的进度验证与停滞检测。"""

from __future__ import annotations

import json
from typing import Any

from app.models.contracts import ActionType, AgentAction, ProgressAssessment, ProgressStatus


class ProgressVerifier:
    """根据动作和观察结果判断运行是否仍在推进。"""

    def __init__(
        self,
        *,
        replan_after_stalls: int = 2,
        stop_after_stalls: int = 3,
    ) -> None:
        """配置连续停滞后的重新规划和强制终止阈值。"""
        if replan_after_stalls < 1:
            raise ValueError("replan_after_stalls must be at least 1")
        if stop_after_stalls < replan_after_stalls:
            raise ValueError("stop_after_stalls must not be lower than replan_after_stalls")

        self._replan_after_stalls = replan_after_stalls
        self._stop_after_stalls = stop_after_stalls

    def assess(
        self,
        *,
        action: AgentAction,
        observation: dict[str, Any] | None,
        previous_fingerprints: list[str],
        consecutive_stalls: int,
    ) -> ProgressAssessment:
        """对本轮动作给出进度结论，且不修改调用方状态。"""
        if action.action_type is ActionType.FINAL_ANSWER:
            return ProgressAssessment(
                status=ProgressStatus.COMPLETED,
                reason="模型已提出最终回答。",
                consecutive_stalls=0,
            )

        if observation is None or not observation.get("result"):
            return self._stalled(
                reason="当前动作没有产生可用观察结果。",
                fingerprint=None,
                consecutive_stalls=consecutive_stalls,
            )

        fingerprint = self._fingerprint(action, observation)
        if fingerprint in previous_fingerprints:
            return self._stalled(
                reason="重复动作返回了与历史相同的观察结果。",
                fingerprint=fingerprint,
                consecutive_stalls=consecutive_stalls,
            )

        return ProgressAssessment(
            status=ProgressStatus.PROGRESSED,
            reason="动作产生了新的观察结果。",
            fingerprint=fingerprint,
            consecutive_stalls=0,
        )

    def _stalled(
        self,
        *,
        reason: str,
        fingerprint: str | None,
        consecutive_stalls: int,
    ) -> ProgressAssessment:
        """创建包含重新规划和终止建议的停滞结论。"""
        updated_stalls = consecutive_stalls + 1
        return ProgressAssessment(
            status=ProgressStatus.STALLED,
            reason=reason,
            fingerprint=fingerprint,
            consecutive_stalls=updated_stalls,
            should_replan=updated_stalls >= self._replan_after_stalls,
            should_stop=updated_stalls >= self._stop_after_stalls,
        )

    @staticmethod
    def _fingerprint(action: AgentAction, observation: dict[str, Any]) -> str:
        """规范化动作和观察结果，保证相同输入具有相同指纹。"""
        payload = {
            "action_type": action.action_type.value,
            "tool_args": action.tool_args,
            "tool_name": action.tool_name,
            "observation": observation,
        }
        return json.dumps(
            payload,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
