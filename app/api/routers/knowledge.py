"""知识库目录与 Markdown 入库 API。"""

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.knowledge import (
    CreateKnowledgeDocumentRequest,
    KnowledgeCatalogResponse,
    KnowledgeDocumentSummary,
)
from app.config import Settings
from app.models.contracts import KnowledgeDocument
from app.rag.documents import load_markdown_chunks
from app.rag.search import KnowledgeSearcher

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


@router.get(
    "/knowledge",
    response_model=KnowledgeCatalogResponse,
    status_code=status.HTTP_200_OK,
    summary="查看知识库目录",
)
async def get_knowledge_catalog(request: Request) -> KnowledgeCatalogResponse:
    """返回可检索文档的最小目录，不返回正文或向量内容。"""
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("application settings are not configured")

    return catalog_from_directory(settings.knowledge_source_directory)


def catalog_from_directory(directory: Path) -> KnowledgeCatalogResponse:
    """从 Markdown 源目录生成仅含标题和片段数的公开目录。"""
    chunks = load_markdown_chunks(directory)
    documents: dict[str, KnowledgeDocumentSummary] = {}
    for chunk in chunks:
        existing = documents.get(chunk.source_id)
        if existing is None:
            documents[chunk.source_id] = KnowledgeDocumentSummary(
                title=chunk.metadata.get("title", chunk.source_id),
                chunk_count=1,
            )
        else:
            existing.chunk_count += 1

    summaries = sorted(documents.values(), key=lambda document: document.title)
    return KnowledgeCatalogResponse(
        document_count=len(summaries),
        chunk_count=len(chunks),
        documents=summaries,
    )


@router.post(
    "/knowledge",
    response_model=KnowledgeCatalogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新增知识文档",
)
async def create_knowledge_document(
    payload: CreateKnowledgeDocumentRequest, request: Request
) -> KnowledgeCatalogResponse:
    """保存 Markdown 并在已配置 Embedding 时同步写入 Milvus。"""
    settings = request.app.state.settings
    searcher = getattr(request.app.state, "knowledge_searcher", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("application settings are not configured")
    if not isinstance(searcher, KnowledgeSearcher):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="knowledge ingestion is not configured",
        )

    directory = settings.knowledge_source_directory
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"workspace-{uuid4().hex}.md"
    path.write_text(
        f"---\ntitle: {payload.title}\n---\n\n# {payload.title}\n\n{payload.content}\n",
        encoding="utf-8",
    )
    try:
        chunks = load_markdown_chunks(directory)
        document = KnowledgeDocument(
            source_id=path.stem,
            content=f"# {payload.title}\n\n{payload.content}",
            metadata={"title": payload.title},
        )
        searcher.ingest_document(document, all_chunks=chunks)
    except Exception as error:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="knowledge ingestion is temporarily unavailable",
        ) from error
    return catalog_from_directory(directory)
