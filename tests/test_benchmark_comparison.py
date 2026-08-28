"""Harness 基准结果比较的验收测试。"""

import pytest

from app.harness.benchmark import compare_benchmark_results
from app.models.contracts import BenchmarkCaseResult, BenchmarkMetrics, BenchmarkResult


def make_result(*, case_ids: list[str], score: float, completion_rate: float) -> BenchmarkResult:
    """构造只包含比较函数所需字段的确定性基准结果。"""
    return BenchmarkResult.model_construct(
        passed=score == 1.0,
        score=score,
        case_results=[BenchmarkCaseResult.model_construct(case_id=case_id) for case_id in case_ids],
        metrics=BenchmarkMetrics(
            run_count=len(case_ids),
            completed_run_count=round(len(case_ids) * completion_rate),
            completion_rate=completion_rate,
            trajectory_pass_rate=completion_rate,
            average_tool_calls=2.0,
            duplicate_tool_call_rate=0.0,
            average_model_calls=3.0,
            average_used_tokens=50.0,
            average_context_chars=120.0,
            terminal_status_counts={"completed": round(len(case_ids) * completion_rate)},
        ),
    )


def test_comparison_uses_full_as_baseline_and_calculates_metric_deltas() -> None:
    """消融结果必须相对 full 输出分数和指标差异。"""
    report = compare_benchmark_results(
        {
            "without_context_manager": make_result(
                case_ids=["payment", "order"], score=0.75, completion_rate=0.5
            ),
            "full": make_result(case_ids=["payment", "order"], score=1.0, completion_rate=1.0),
        }
    )

    assert report["baseline_profile"] == "full"
    assert [profile["profile"] for profile in report["profiles"]] == [
        "full",
        "without_context_manager",
    ]
    assert report["profiles"][1]["deltas_from_full"] == {
        "score": -0.25,
        "run_count": 0,
        "completed_run_count": -1,
        "completion_rate": -0.5,
        "trajectory_pass_rate": -0.5,
        "average_tool_calls": 0.0,
        "duplicate_tool_call_rate": 0.0,
        "average_model_calls": 0.0,
        "average_used_tokens": 0.0,
        "average_context_chars": 0.0,
    }


def test_comparison_rejects_different_case_sets() -> None:
    """不同样本集的指标没有可比性，必须停止输出结论。"""
    with pytest.raises(ValueError, match="do not match full"):
        compare_benchmark_results(
            {
                "full": make_result(case_ids=["payment"], score=1.0, completion_rate=1.0),
                "without_progress_verifier": make_result(
                    case_ids=["order"], score=1.0, completion_rate=1.0
                ),
            }
        )
