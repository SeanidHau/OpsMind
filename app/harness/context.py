"""模型上下文的构建、去重与预算限制。"""

from __future__ import annotations

import json
from typing import Any

from app.models.contracts import (
    ContextItem,
    ContextSnapshot,
    ContextSource,
    DiagnosisState,
)


class ContextManager:
    """从完整诊断状态构建模型可见的最小上下文。"""

    def __init__(self, *, max_chars: int = 4_000, max_items: int = 8) -> None:
        """配置单轮上下文的字符数和条目数上限。"""
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than 0")
        if max_items <= 0:
            raise ValueError("max_items must be greater than 0")

        self.max_chars = max_chars
        self.max_items = max_items

    def build(self, state: DiagnosisState) -> ContextSnapshot:
        """按优先级选择，去重并截断当前状态中的上下文。"""
        candidates = self._collect_candidates(state)
        unique_candidates = self._deduplicate(candidates)

        items: list[ContextItem] = []
        total_chars = 0
        truncated = False

        for candidate in unique_candidates:
            if len(items) >= self.max_items:
                truncated = True
                break

            remaining_chars = self.max_chars - total_chars
            if remaining_chars <= 0:
                truncated = True
                break

            content = candidate.content
            if len(content) > remaining_chars:
                # 字符预算不足时保留当前最高优先级条目的前半部分。
                items.append(candidate.model_copy(update={"content": content[:remaining_chars]}))
                total_chars += remaining_chars
                truncated = True
                break

            items.append(candidate)
            total_chars += len(content)

        return ContextSnapshot(
            items=items,
            total_chars=total_chars,
            truncated=truncated,
        )

    def _collect_candidates(self, state: DiagnosisState) -> list[ContextItem]:
        """将领域状态转换为带固定优先级的候选上下文条目。"""
        candidates = [
            ContextItem(
                source=ContextSource.TASK,
                reference="user_query",
                content=state["user_query"],
                priority=100,
            )
        ]

        candidates.extend(
            ContextItem(
                source=ContextSource.PLAN,
                reference=f"plan:{item.id}",
                content=f"{item.status}: {item.title}",
                priority=90,
            )
            for item in state["plan"]
        )
        candidates.extend(
            ContextItem(
                source=ContextSource.ERROR,
                reference=f"error:{index}",
                content=error,
                priority=80,
            )
            for index, error in enumerate(state["errors"])
        )
        candidates.extend(
            ContextItem(
                source=ContextSource.EVIDENCE,
                reference=f"evidence:{evidence.evidence_id}",
                content=evidence.content,
                priority=70,
            )
            for evidence in state["evidence"]
        )
        candidates.extend(
            ContextItem(
                source=ContextSource.TOOL_RESULT,
                reference=f"tool:{result.get('tool_name', 'unknown')}",
                content=self._serialize(result),
                priority=60,
            )
            for result in reversed(state["tool_results"])
        )

        # 保持统一优先级内的原始顺序
        return sorted(candidates, key=lambda item: item.priority, reverse=True)

    @staticmethod
    def _deduplicate(candidates: list[ContextItem]) -> list[ContextItem]:
        """删除完全相同的来源、引用和内容，避免重复上下文。"""
        unique_items: list[ContextItem] = []
        seen: set[tuple[ContextSource, str, str]] = set()

        for candidate in candidates:
            key = (candidate.source, candidate.reference, candidate.content)
            if key in seen:
                continue
            seen.add(key)
            unique_items.append(candidate)

        return unique_items

    @staticmethod
    def _serialize(value: Any) -> str:
        """以稳定 JSON 表示结构化证据，方便去重和后续追踪。"""
        return json.dumps(
            value,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
