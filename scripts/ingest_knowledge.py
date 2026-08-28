"""将 Markdown 知识目录幂等写入 Milvus。"""

from __future__ import annotations

import argparse
from pathlib import Path

from pymilvus import MilvusClient  # type: ignore[import-untyped]

from app.config import get_settings
from app.rag.documents import markdown_paths
from app.rag.embeddings import create_embedding_client
from app.rag.ingestion import KnowledgeIngestor
from app.rag.milvus_store import MilvusVectorStore


def ingest_directory(ingestor: KnowledgeIngestor, source_directory: Path) -> tuple[int, int]:
    """按文件名稳定顺序入库一个目录中的 Markdown 文档。"""
    paths = markdown_paths(source_directory)
    chunk_count = sum(len(ingestor.ingest_markdown(path)) for path in paths)
    return len(paths), chunk_count


def parse_args() -> argparse.Namespace:
    """解析可选的知识目录参数。"""
    parser = argparse.ArgumentParser(description="将 Markdown 知识文档写入 Milvus")
    parser.add_argument("--source-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    """按当前环境配置创建客户端并执行幂等入库。"""
    args = parse_args()
    settings = get_settings()
    embedder = create_embedding_client(settings)
    if embedder is None:
        raise SystemExit("EMBEDDING_MODEL must be configured before ingesting knowledge")

    client = MilvusClient(uri=str(settings.milvus_url))
    try:
        document_count, chunk_count = ingest_directory(
            KnowledgeIngestor(
                embedder=embedder,
                vector_store=MilvusVectorStore(
                    client=client,
                    collection_name=settings.knowledge_collection_name,
                    vector_size=settings.embedding_vector_size,
                ),
            ),
            args.source_dir or settings.knowledge_source_directory,
        )
    finally:
        client.close()

    print(f"ingested {document_count} documents and {chunk_count} chunks")


if __name__ == "__main__":
    main()
