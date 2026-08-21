"""将工具观察结果转换为稳定的结构化证据。"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.models.contracts import EvidenceItem


class EvidenceCollector:
    """规范化工具观察结果，并生成可去重、可引用的证据。"""

    def __init__(self, *, max_content_chars: int = 8_000) -> None:
        """设置允许进入模型上下文的单条证据内容上限。"""
        if max_content_chars <= 0:
            raise ValueError("max_content_chars must be greater than 0")

        self._max_content_chars = max_content_chars

    def collect(
        self,
        *,
        tool_name: str,
        observation: dict[str, Any],
    ) -> EvidenceItem:
        """从完整观察结果生成稳定 ID 和受限展示内容。"""
        if not tool_name.strip():
            raise ValueError("tool_name must not be blank")

        # 固定键顺序和紧凑 JSON，确保语义相同的字典生成相同证据 ID。
        normalized = json.dumps(
            observation,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        evidence_id = sha256(f"{tool_name}\n{normalized}".encode()).hexdigest()
        truncated = len(normalized) > self._max_content_chars

        return EvidenceItem(
            evidence_id=evidence_id,
            tool_name=tool_name,
            content=normalized[: self._max_content_chars],
            truncated=truncated,
        )


class EvidenceGate:
    """检验最终回答是否拥有足够的结构化证据。"""

    def __init__(self, *, min_evidence: int = 1) -> None:
        """设置允许完成诊断所需的最小证据数量。"""
        if min_evidence <= 0:
            raise ValueError("min_evidence must be greater than 0")

        self._min_evidence = min_evidence

    def validate(self, evidence: list[EvidenceItem]) -> str | None:
        """证据不足时返回阻断原因，满足门槛时返回 None。"""
        if len(evidence) < self._min_evidence:
            return f"final answer requires at least {self._min_evidence} evidence items"
        return None
