"""知识库目录的只读 API。"""

from fastapi import APIRouter, Request, status

from app.api.schemas.knowledge import KnowledgeCatalogResponse, KnowledgeDocumentSummary
from app.config import Settings
from app.rag.documents import load_markdown_chunks

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

    chunks = load_markdown_chunks(settings.knowledge_source_directory)
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
