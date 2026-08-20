"""Markdown 知识文档加载与确定性分块。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from langchain_core.documents import Document

from app.models.contracts import KnowledgeChunk, KnowledgeDocument


class MarkdownKnowledgeLoader:
    """读取 Markdown Front Matter、标题与正文。"""

    def load(self, path: Path) -> KnowledgeDocument:
        """加载单个 Markdown 文件并返回统一知识文档。"""
        rwa_content = path.read_text(encoding="utf-8")
        metadata, content = self._parse_front_matter(rwa_content)

        title = self._extract_title(content)
        if title is not None and "title" not in metadata:
            metadata["title"] = title

        return KnowledgeDocument(
            source_id=path.stem,
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def _parse_front_matter(raw_content: str) -> tuple[dict[str, str], str]:
        """解析 YAML 风格的简单键值 Front Matter。"""
        lines = raw_content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw_content.strip()

        closing_index: int | None = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                closing_index = index
                break

        if closing_index is None:
            raise ValueError("front matter is missing a closing delimiter")

        metadata: dict[str, str] = {}
        for line in lines[1:closing_index]:
            if ":" not in line:
                raise ValueError(f"invalid front matter line: {line}")

            key, value = line.split(":", maxsplit=1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError("front matter key must not be empty")

            metadata[key] = value

        content = "\n".join(lines[closing_index + 1 :]).strip()
        if not content:
            raise ValueError("markdown document body must not be empty")

        return metadata, content

    @staticmethod
    def _extract_title(content: str) -> str | None:
        """提取第一个一级标题，供检索结果和来源引用展示。"""
        for line in content.splitlines():
            if line.startswith("# "):
                return line[2:].strip() or None
        return None


class MarkdownChunker:
    """按固定字符窗口切分文档，并生成稳定分块ID。"""

    def __init__(self, *, chunk_size: int = 800, chunk_overlap: int = 100) -> None:
        """配置分块大小和相邻分块的重叠字符数。"""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        """将文档切分为有重叠窗口的稳定分块。"""
        chunks: list[KnowledgeChunk] = []
        start = 0
        index = 0

        while start < len(document.content):
            end = min(start + self._chunk_size, len(document.content))
            content = document.content[start:end]
            chunk_id = self._build_chunk_id(
                source_id=document.source_id,
                index=index,
                content=content,
            )

            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    source_id=document.source_id,
                    index=index,
                    content=content,
                    metadata=dict(document.metadata),
                )
            )

            if end == len(document.content):
                break

            # 重叠窗口保证相邻片段不会截断关键上下文
            start = end - self._chunk_overlap
            index += 1

        return chunks

    @staticmethod
    def to_langchain_documents(chunks: list[KnowledgeChunk]) -> list[Document]:
        """将统一分块转为LangChain 检索组件使用的 Document。"""
        return [
            Document(
                page_content=chunk.content,
                metadata={
                    **chunk.metadata,
                    "source_id": chunk.source_id,
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.index,
                },
            )
            for chunk in chunks
        ]

    @staticmethod
    def _build_chunk_id(*, source_id: str, index: int, content: str) -> str:
        """使用来源、索引和内容生成可复现的 SHA-256 分块标识。"""
        payload = f"{source_id}:{index}:{content}".encode()
        return sha256(payload).hexdigest()
