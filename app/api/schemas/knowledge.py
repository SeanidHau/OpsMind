"""知识库目录的 HTTP 响应模型。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeDocumentSummary(BaseModel):
    """工作台可展示的一份知识文档摘要。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    chunk_count: int = Field(ge=0)


class KnowledgeDocumentResponse(BaseModel):
    """一份可在工作台阅读的 Markdown 知识正文。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)


class KnowledgeCatalogResponse(BaseModel):
    """知识库加载状态与公开目录。"""

    model_config = ConfigDict(extra="forbid")

    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    documents: list[KnowledgeDocumentSummary]


class CreateKnowledgeDocumentRequest(BaseModel):
    """由工作台新增的一份 Markdown 知识文档。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("title", "content")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """去除无意义留白，拒绝空知识内容。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("title")
    @classmethod
    def title_must_be_single_line(cls, value: str) -> str:
        """标题将写入 Front Matter，不能包含换行。"""
        if "\n" in value or "\r" in value:
            raise ValueError("title must be a single line")
        return value
