"""对已入库的知识库执行只读离线检索评测。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pymilvus import MilvusClient  # type: ignore[import-untyped]

from app.config import get_settings
from app.rag.bm25 import BM25Retriever
from app.rag.documents import load_markdown_chunks
from app.rag.embeddings import create_embedding_client
from app.rag.evaluation import RetrievalEvaluator, load_retrieval_cases
from app.rag.milvus_store import MilvusVectorStore
from app.rag.search import KnowledgeSearcher

DEFAULT_CASES_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "evaluations" / "retrieval_cases.json"
)


def parse_args() -> argparse.Namespace:
    """解析评测样本文件与返回数量参数。"""
    parser = argparse.ArgumentParser(description="对已入库知识库执行只读检索评测")
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--fail-on-miss",
        action="store_true",
        help="任一固定样本未命中时，以状态码 1 退出",
    )
    return parser.parse_args()


def quality_gate_exit_code(*, passed: bool, fail_on_miss: bool) -> int:
    """仅在显式启用质量门禁且存在漏召回时返回失败状态码。"""
    return int(fail_on_miss and not passed)


def main() -> int:
    """输出带样本明细的检索指标 JSON，不修改 Milvus 数据。"""
    args = parse_args()
    settings = get_settings()
    embedder = create_embedding_client(settings)
    if embedder is None:
        raise SystemExit("EMBEDDING_MODEL must be configured before evaluating retrieval")
    searcher = KnowledgeSearcher(
        embedder=embedder,
        vector_store=MilvusVectorStore(
            client=MilvusClient(uri=str(settings.milvus_url)),
            collection_name=settings.knowledge_collection_name,
            vector_size=settings.embedding_vector_size,
        ),
        keyword_retriever=BM25Retriever(load_markdown_chunks(settings.knowledge_source_directory)),
    )

    try:
        evaluation = RetrievalEvaluator().evaluate(
            cases=load_retrieval_cases(args.cases_file),
            searcher=searcher,
            top_k=args.top_k,
        )
    finally:
        searcher.close()

    print(json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False))
    return quality_gate_exit_code(
        passed=evaluation.passed,
        fail_on_miss=args.fail_on_miss,
    )


if __name__ == "__main__":
    raise SystemExit(main())
