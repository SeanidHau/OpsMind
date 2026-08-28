"""知识目录入库脚本的验收测试。"""

from pathlib import Path

import pytest

from scripts.ingest_knowledge import ingest_directory


class RecordingIngestor:
    """记录脚本传入的文档顺序，不访问 Embedding 或 Milvus。"""

    def __init__(self) -> None:
        self.paths: list[Path] = []

    def ingest_markdown(self, path: Path) -> list[object]:
        self.paths.append(path)
        return [object(), object()]


def test_ingest_directory_sorts_markdown_files_and_counts_chunks(tmp_path: Path) -> None:
    """脚本必须稳定遍历 Markdown 文件并汇总实际写入分块数。"""
    (tmp_path / "z-runbook.md").write_text("# Z", encoding="utf-8")
    (tmp_path / "a-runbook.md").write_text("# A", encoding="utf-8")
    ingestor = RecordingIngestor()

    document_count, chunk_count = ingest_directory(ingestor, tmp_path)  # type: ignore[arg-type]

    assert [path.name for path in ingestor.paths] == ["a-runbook.md", "z-runbook.md"]
    assert (document_count, chunk_count) == (2, 4)


@pytest.mark.parametrize("files", [[], ["notes.txt"]])
def test_ingest_directory_rejects_directories_without_markdown(
    tmp_path: Path,
    files: list[str],
) -> None:
    """空目录或非 Markdown 文件不得被误认为可入库知识。"""
    for name in files:
        (tmp_path / name).write_text("ignored", encoding="utf-8")

    with pytest.raises(ValueError, match="contains no Markdown"):
        ingest_directory(RecordingIngestor(), tmp_path)  # type: ignore[arg-type]
