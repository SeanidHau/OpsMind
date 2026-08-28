"""运行固定端到端诊断基准，并输出可供 CI 读取的 JSON。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pymilvus import MilvusClient  # type: ignore[import-untyped]

from app.config import get_settings
from app.diagnosis.providers import create_action_provider
from app.diagnosis.runtime import HarnessProfile, create_harness_diagnosis_runner
from app.harness.benchmark import (
    HarnessBenchmarkSubject,
    OfflineBenchmarkRunner,
    load_benchmark_cases,
)
from app.harness.snapshot import InMemoryRunArchive, PostgresRunArchive, RunArchive
from app.observability.langsmith import create_langsmith_tracer
from app.rag.bm25 import BM25Retriever
from app.rag.documents import load_markdown_chunks
from app.rag.embeddings import create_embedding_client
from app.rag.milvus_store import MilvusVectorStore
from app.rag.search import KnowledgeSearcher
from app.scenarios.defaults import create_default_scenario_store
from app.tools.knowledge import register_knowledge_tools
from app.tools.registry import ToolRegistry
from app.tools.scenarios import register_scenario_tools

DEFAULT_CASES_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "evaluations" / "diagnosis_cases.json"
)


def parse_args() -> argparse.Namespace:
    """解析样本文件、Harness 组件配置和可选的失败状态码开关。"""
    parser = argparse.ArgumentParser(description="运行固定端到端诊断基准")
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in HarnessProfile],
        default=HarnessProfile.FULL.value,
        help="选择 Context Manager 或 Progress Verifier 的消融配置",
    )
    parser.add_argument(
        "--fail-on-failure",
        action="store_true",
        help="任一样本未通过时，以状态码 1 退出",
    )
    return parser.parse_args()


def benchmark_exit_code(*, passed: bool, fail_on_failure: bool) -> int:
    """仅在显式启用门禁且基准失败时返回失败状态码。"""
    return int(fail_on_failure and not passed)


def create_run_archive() -> RunArchive:
    """按当前配置选择内存或 PostgreSQL 快照归档。"""
    settings = get_settings()
    if settings.run_archive_backend == "postgres":
        return PostgresRunArchive(str(settings.database_url))
    return InMemoryRunArchive()


def create_knowledge_searcher() -> KnowledgeSearcher | None:
    """在配置 Embedding 时建立与应用相同的混合知识检索器。"""
    settings = get_settings()
    embedder = create_embedding_client(settings)
    if embedder is None:
        return None
    return KnowledgeSearcher(
        embedder=embedder,
        vector_store=MilvusVectorStore(
            client=MilvusClient(uri=str(settings.milvus_url)),
            collection_name=settings.knowledge_collection_name,
            vector_size=settings.embedding_vector_size,
        ),
        keyword_retriever=BM25Retriever(load_markdown_chunks(settings.knowledge_source_directory)),
    )


async def main() -> int:
    """装配当前运行时并输出固定样本的端到端基准结果。"""
    args = parse_args()
    settings = get_settings()
    registry = ToolRegistry()
    register_scenario_tools(registry, create_default_scenario_store())
    knowledge_searcher = create_knowledge_searcher()
    if knowledge_searcher is not None:
        register_knowledge_tools(registry, knowledge_searcher)

    run_archive: RunArchive | None = None
    try:
        action_provider = create_action_provider(
            settings,
            tool_definitions=registry.definitions(),
        )
        if action_provider is None:
            raise SystemExit("LLM_PROVIDER must be configured before running the benchmark")

        run_archive = create_run_archive()
        if isinstance(run_archive, PostgresRunArchive):
            await run_archive.initialize()

        result = await OfflineBenchmarkRunner().run(
            cases=load_benchmark_cases(args.cases_file),
            subject=HarnessBenchmarkSubject(
                runner=create_harness_diagnosis_runner(
                    action_provider=action_provider,
                    tool_registry=registry,
                    run_archive=run_archive,
                    profile=HarnessProfile(args.profile),
                    tracer=create_langsmith_tracer(settings),
                )
            ),
        )
    finally:
        if isinstance(run_archive, PostgresRunArchive):
            await run_archive.close()
        if knowledge_searcher is not None:
            knowledge_searcher.close()

    print(
        json.dumps(
            {"profile": args.profile, **result.model_dump(mode="json")},
            ensure_ascii=False,
        )
    )
    return benchmark_exit_code(
        passed=result.passed,
        fail_on_failure=args.fail_on_failure,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
