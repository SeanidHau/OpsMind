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
        "documents": [
            {
                "document_id": "payment",
                "title": "支付连接池处理手册",
                "chunk_count": 1,
            }
        ],
    }


def test_knowledge_document_endpoint_returns_markdown_content(tmp_path) -> None:
    """正文按文档读取，目录外路径不可被访问。"""
    (tmp_path / "payment.md").write_text(
        "# 支付连接池处理手册\n\n先检查连接池利用率。", encoding="utf-8"
    )
    settings = Settings(_env_file=None, knowledge_source_directory=tmp_path)

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/v1/knowledge/payment")
        invalid_response = client.get("/api/v1/knowledge/..")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": "payment",
        "title": "支付连接池处理手册",
        "content": "# 支付连接池处理手册\n\n先检查连接池利用率。",
    }
    assert invalid_response.status_code == 404
