"""Qdrant 向量存储适配器。"""

from __future__ import annotations

from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from app.models.contracts import KnowledgeChunk, RetrievalHit, VectorizedChunk


class QdrantVectorStore:
    """将稳定知识分块写入 Qdrant，并还原为统一检索结果。"""

    def __init__(
        self,
        *,
        client: QdrantClient,
        collection_name: str,
        vector_size: int,
    ) -> None:
        """保存 Qdrant client 与集合的固定向量维度。"""
        if not collection_name.strip():
            raise ValueError("collection_name must not be blank")
        if vector_size <= 0:
            raise ValueError("vector_size must be greater than 0")

        self._client = client
        self._collection_name = collection_name
        self._vector_size = vector_size

    def upsert(self, records: list[VectorizedChunk]) -> None:
        """创建集合（如需要）并幂等写入向量分块。"""
        self._ensure_collection()

        points: list[models.PointStruct] = []
        for record in records:
            if len(record.vector) != self._vector_size:
                raise ValueError("record vector dimension does not match the collection")

            # 使用 chunk_id 生成固定 UUID，使重复写入覆盖原点而非新增重复记录。
            point_id = str(uuid5(NAMESPACE_URL, record.chunk.chunk_id))
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=record.vector,
                    payload=self._payload_from_record(record),
                )
            )

        if points:
            self._client.upsert(
                collection_name=self._collection_name,
                points=points,
                wait=True,
            )

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        """查询 Qdrant，并将命中点转换为 `RetrievalHit`。"""
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        if len(query_vector) != self._vector_size:
            raise ValueError("query_vector dimension does not match the collection")

        self._ensure_collection()
        response = self._client.query_points(
            collection_name=self._collection_name,
            query=query_vector,
            query_filter=self._build_filter(metadata_filter),
            limit=top_k,
            with_payload=True,
        )

        # Qdrant 可能返回零或负相似度；统一检索契约只保留正相关证据。
        positive_points = [
            point for point in response.points if point.score > 0 and point.payload is not None
        ]

        return [
            RetrievalHit(
                chunk=self._chunk_from_payload(cast(dict[str, Any], point.payload)),
                score=float(point.score),
                rank=rank,
            )
            for rank, point in enumerate(positive_points, start=1)
        ]

    def _ensure_collection(self) -> None:
        """仅在集合不存在时创建余弦距离集合。"""
        if self._client.collection_exists(self._collection_name):
            return

        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=models.VectorParams(
                size=self._vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    @staticmethod
    def _payload_from_record(record: VectorizedChunk) -> dict[str, Any]:
        """将统一分块契约映射为 Qdrant payload。"""
        return {
            "chunk_id": record.chunk.chunk_id,
            "source_id": record.chunk.source_id,
            "index": record.chunk.index,
            "content": record.chunk.content,
            "metadata": record.chunk.metadata,
        }

    @staticmethod
    def _chunk_from_payload(payload: dict[str, Any]) -> KnowledgeChunk:
        """从 Qdrant payload 重建稳定知识分块。"""
        raw_metadata = payload.get("metadata", {})
        if not isinstance(raw_metadata, dict):
            raise ValueError("Qdrant payload metadata must be an object")

        return KnowledgeChunk(
            chunk_id=str(payload["chunk_id"]),
            source_id=str(payload["source_id"]),
            index=int(payload["index"]),
            content=str(payload["content"]),
            metadata={str(key): str(value) for key, value in raw_metadata.items()},
        )

    @staticmethod
    def _build_filter(
        metadata_filter: dict[str, str] | None,
    ) -> models.Filter | None:
        """将精确匹配元数据转换为 Qdrant 嵌套字段过滤条件。"""
        if not metadata_filter:
            return None

        return models.Filter(
            must=[
                models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))
                for key, value in sorted(metadata_filter.items())
            ]
        )
