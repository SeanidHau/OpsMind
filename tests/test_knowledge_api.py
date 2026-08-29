"""知识库目录 API 的验收测试。"""

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings


def test_knowledge_endpoint_returns_document_catalog_without_content(tmp_path) -> None:
    """目录仅公开标题和分块数量，不泄露知识正文。"""
    knowledge_path = tmp_path / "payment.md"
    knowledge_path.write_text(
        "---\ntitle: 支付连接池处理手册\n---\n\n# 支付连接池处理手册\n\n内部处理细节。",
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, knowledge_source_directory=tmp_path)

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/v1/knowledge")

    assert response.status_code == 200
    assert response.json() == {
        "document_count": 1,
        "chunk_count": 1,
        "documents": [{"title": "支付连接池处理手册", "chunk_count": 1}],
    }
