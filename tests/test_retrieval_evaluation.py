"""固定知识样本检索评测的验收测试。"""

from pathlib import Path

import pytest

from app.models.contracts import FusedRetrievalHit, KnowledgeChunk, RetrievalEvaluationCase
from app.rag.evaluation import RetrievalEvaluator, load_retrieval_cases
from scripts.evaluate_retrieval import quality_gate_exit_code


class FixedSearcher:
    """按查询返回固定来源，并记录调用参数。"""

    def __init__(self, source_ids_by_query: dict[str, list[str]]) -> None:
        self._source_ids_by_query = source_ids_by_query
        self.calls: list[tuple[str, int, dict[str, str] | None]] = []

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[FusedRetrievalHit]:
        self.calls.append((query, top_k, metadata_filter))
        return [
            FusedRetrievalHit(
                chunk=KnowledgeChunk(
                    chunk_id=f"{source_id}-{rank}",
                    source_id=source_id,
                    index=rank - 1,
                    content=f"{source_id} content",
                ),
                score=1 / rank,
                rank=rank,
                retriever_names=["vector"],
            )
            for rank, source_id in enumerate(self._source_ids_by_query[query], start=1)
        ]


def test_evaluator_calculates_recall_and_mrr_with_per_case_ranks() -> None:
    """命中、二位命中和漏召回应分别影响 Recall@K 与 MRR。"""
    cases = [
        RetrievalEvaluationCase(
            case_id="first",
            query="first query",
            expected_source_id="first-source",
            metadata_filter={"service": "first-service"},
        ),
        RetrievalEvaluationCase(
            case_id="second",
            query="second query",
            expected_source_id="second-source",
        ),
        RetrievalEvaluationCase(
            case_id="missing",
            query="missing query",
            expected_source_id="missing-source",
        ),
    ]
    searcher = FixedSearcher(
        {
            "first query": ["first-source"],
            "second query": ["other-source", "second-source"],
            "missing query": ["other-source"],
        }
    )

    evaluation = RetrievalEvaluator().evaluate(cases=cases, searcher=searcher, top_k=2)

    assert evaluation.passed is False
    assert evaluation.recall_at_k == pytest.approx(2 / 3)
    assert evaluation.mean_reciprocal_rank == pytest.approx(0.5)
    assert [result.rank for result in evaluation.case_results] == [1, 2, None]
    assert searcher.calls == [
        ("first query", 2, {"service": "first-service"}),
        ("second query", 2, None),
        ("missing query", 2, None),
    ]


def test_loader_reads_committed_retrieval_cases() -> None:
    """提交的离线样本必须可被统一契约读取。"""
    cases = load_retrieval_cases(Path("data/evaluations/retrieval_cases.json"))

    assert [case.expected_source_id for case in cases] == [
        "order-http-5xx-runbook",
        "payment-connection-pool-runbook",
        "inventory-latency-runbook",
        "recommendation-redis-cache-runbook",
    ]


def test_loader_rejects_empty_case_array(tmp_path: Path) -> None:
    """空样本文件不应生成没有统计意义的评测结果。"""
    path = tmp_path / "cases.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty JSON array"):
        load_retrieval_cases(path)


def test_evaluator_rejects_invalid_input() -> None:
    """空样本和非法 top_k 应在调用检索器前失败。"""
    searcher = FixedSearcher({})

    with pytest.raises(ValueError, match="at least one"):
        RetrievalEvaluator().evaluate(cases=[], searcher=searcher)
    with pytest.raises(ValueError, match="top_k"):
        RetrievalEvaluator().evaluate(
            cases=[
                RetrievalEvaluationCase(case_id="case", query="query", expected_source_id="source")
            ],
            searcher=searcher,
            top_k=0,
        )


@pytest.mark.parametrize(
    ("passed", "fail_on_miss", "expected_exit_code"),
    [
        (True, False, 0),
        (True, True, 0),
        (False, False, 0),
        (False, True, 1),
    ],
)
def test_quality_gate_fails_only_for_requested_misses(
    passed: bool,
    fail_on_miss: bool,
    expected_exit_code: int,
) -> None:
    """本地观察默认不阻断，CI 可显式将漏召回视为失败。"""
    assert quality_gate_exit_code(passed=passed, fail_on_miss=fail_on_miss) == expected_exit_code
