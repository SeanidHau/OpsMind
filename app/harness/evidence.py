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
