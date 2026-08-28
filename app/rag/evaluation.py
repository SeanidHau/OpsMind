"""固定知识样本的确定性离线检索评测。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from app.models.contracts import (
    FusedRetrievalHit,
    RetrievalCaseResult,
    RetrievalEvaluation,
    RetrievalEvaluationCase,
)


class RetrievalSubject(Protocol):
    """离线评测所需的最小知识检索接口。"""

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[FusedRetrievalHit]:
        """返回与查询相关、带来源标识的分块。"""


def load_retrieval_cases(path: Path) -> list[RetrievalEvaluationCase]:
    """从 JSON 数组读取并校验固定检索评测样本。"""
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("retrieval evaluation cases must be a non-empty JSON array")
    return [RetrievalEvaluationCase.model_validate(case) for case in raw_cases]


class RetrievalEvaluator:
    """以目标来源首次出现的位置计算 Recall@K 和 MRR。"""

    def evaluate(
        self,
        *,
        cases: list[RetrievalEvaluationCase],
        searcher: RetrievalSubject,
        top_k: int = 3,
    ) -> RetrievalEvaluation:
        """运行全部固定样本，并保留每条样本的可复核结果。"""
        if not cases:
            raise ValueError("at least one retrieval evaluation case is required")
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")

        case_results = [
            self._evaluate_case(case=case, searcher=searcher, top_k=top_k) for case in cases
        ]
        hit_count = sum(result.rank is not None for result in case_results)
        reciprocal_rank_sum = sum(1 / result.rank for result in case_results if result.rank)

        return RetrievalEvaluation(
            passed=hit_count == len(case_results),
            recall_at_k=hit_count / len(case_results),
            mean_reciprocal_rank=reciprocal_rank_sum / len(case_results),
            top_k=top_k,
            case_results=case_results,
        )

    @staticmethod
    def _evaluate_case(
        *,
        case: RetrievalEvaluationCase,
        searcher: RetrievalSubject,
        top_k: int,
    ) -> RetrievalCaseResult:
        """执行单条样本，并记录目标来源的第一个一基排名。"""
        hits = searcher.search(
            case.query,
            top_k=top_k,
            metadata_filter=case.metadata_filter or None,
        )
        source_ids = [hit.chunk.source_id for hit in hits]
        rank = next(
            (
                index
                for index, source_id in enumerate(source_ids, start=1)
                if source_id == case.expected_source_id
            ),
            None,
        )
        return RetrievalCaseResult(
            case_id=case.case_id,
            expected_source_id=case.expected_source_id,
            retrieved_source_ids=source_ids,
            rank=rank,
        )
