"""比较多个既有端到端诊断基准结果。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.harness.benchmark import compare_benchmark_results
from app.models.contracts import BenchmarkResult


def parse_args() -> argparse.Namespace:
    """解析至少两份已保存的基准 JSON 结果。"""
    parser = argparse.ArgumentParser(description="比较既有 Harness 基准结果")
    parser.add_argument(
        "result_files",
        nargs="+",
        type=Path,
        help="由 scripts.run_benchmark 输出并保存的 JSON 文件",
    )
    return parser.parse_args()


def load_profile_result(path: Path) -> tuple[str, BenchmarkResult]:
    """读取一份带 profile 标识的基准结果。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    profile = raw.pop("profile", None) if isinstance(raw, dict) else None
    if not isinstance(profile, str) or not profile:
        raise ValueError(f"benchmark result {path} is missing a profile")
    return profile, BenchmarkResult.model_validate(raw)


def main() -> int:
    """读取结果、拒绝重复配置，并输出 full 基准下的差异。"""
    args = parse_args()
    results: dict[str, BenchmarkResult] = {}
    for path in args.result_files:
        profile, result = load_profile_result(path)
        if profile in results:
            raise ValueError(f"duplicate benchmark profile: {profile}")
        results[profile] = result

    print(json.dumps(compare_benchmark_results(results), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
