"""知识库目录的 HTTP 响应模型。"""

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocumentSummary(BaseModel):
    """工作台可展示的一份知识文档摘要。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    chunk_count: int = Field(ge=0)


class KnowledgeCatalogResponse(BaseModel):
    """知识库加载状态与公开目录。"""

    model_config = ConfigDict(extra="forbid")

    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    documents: list[KnowledgeDocumentSummary]
