"""Milvus 向量存储适配器。"""

from __future__ import annotations

import json
from typing import Any

from pymilvus import DataType, MilvusClient  # type: ignore[import-untyped]

from app.models.contracts import KnowledgeChunk, RetrievalHit, VectorizedChunk


class MilvusVectorStore:
    """将稳定知识分块写入 Milvus，并还原为统一检索结果。"""

    def __init__(
        self,
        *,
        client: MilvusClient,
        collection_name: str,
        vector_size: int,
    ) -> None:
        """保存 Milvus client 与集合的固定向量维度。"""
        if not collection_name.strip():
            raise ValueError("collection_name must not be blank")
        if vector_size <= 0:
            raise ValueError("vector_size must be greater than 0")

        self._client = client
        self._collection_name = collection_name
        self._vector_size = vector_size

    def upsert(self, records: list[VectorizedChunk]) -> None:
        """创建集合（如需要）并按 chunk ID 幂等写入向量分块。"""
        self._ensure_collection()
        entities: list[dict[str, Any]] = []
        for record in records:
            if len(record.vector) != self._vector_size:
                raise ValueError("record vector dimension does not match the collection")
            entities.append(self._entity_from_record(record))

        if entities:
            self._client.upsert(collection_name=self._collection_name, data=entities)

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 3,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalHit]:
        """查询 Milvus，并将命中实体转换为 `RetrievalHit`。"""
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        if len(query_vector) != self._vector_size:
            raise ValueError("query_vector dimension does not match the collection")

        self._ensure_collection()
        results = self._client.search(
            collection_name=self._collection_name,
            data=[query_vector],
            filter=self._metadata_expression(metadata_filter),
            limit=top_k,
            output_fields=["chunk_id", "source_id", "chunk_index", "content", "metadata"],
            search_params={"metric_type": "COSINE", "params": {}},
        )
        points = results[0] if results else []
        positive_points = [point for point in points if float(point["distance"]) > 0]

        return [
            RetrievalHit(
                chunk=self._chunk_from_entity(point["entity"]),
                score=float(point["distance"]),
                rank=rank,
            )
            for rank, point in enumerate(positive_points, start=1)
        ]

    def _ensure_collection(self) -> None:
        """仅在集合不存在时创建固定 schema 与余弦索引。"""
        if self._client.has_collection(collection_name=self._collection_name):
            return

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            field_name="chunk_id",
            datatype=DataType.VARCHAR,
            is_primary=True,
            max_length=64,
        )
        schema.add_field(field_name="source_id", datatype=DataType.VARCHAR, max_length=200)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8_000)
        schema.add_field(field_name="metadata", datatype=DataType.JSON)
        schema.add_field(
            field_name="vector",
            datatype=DataType.FLOAT_VECTOR,
            dim=self._vector_size,
        )
        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self._client.create_collection(
            collection_name=self._collection_name,
            schema=schema,
            index_params=index_params,
            consistency_level="Strong",
        )

    @staticmethod
    def _entity_from_record(record: VectorizedChunk) -> dict[str, Any]:
        """将统一分块契约映射为 Milvus 静态 schema。"""
        return {
            "chunk_id": record.chunk.chunk_id,
            "source_id": record.chunk.source_id,
            "chunk_index": record.chunk.index,
            "content": record.chunk.content,
            "metadata": record.chunk.metadata,
            "vector": record.vector,
        }

    @staticmethod
    def _chunk_from_entity(entity: dict[str, Any]) -> KnowledgeChunk:
        """从 Milvus 查询实体重建稳定知识分块。"""
        raw_metadata = entity.get("metadata", {})
        if not isinstance(raw_metadata, dict):
            raise ValueError("Milvus metadata must be an object")

        return KnowledgeChunk(
            chunk_id=str(entity["chunk_id"]),
            source_id=str(entity["source_id"]),
            index=int(entity["chunk_index"]),
            content=str(entity["content"]),
            metadata={str(key): str(value) for key, value in raw_metadata.items()},
        )

    @staticmethod
    def _metadata_expression(metadata_filter: dict[str, str] | None) -> str:
        """将精确元数据过滤转换为 Milvus JSON 表达式。"""
        if not metadata_filter:
            return ""

        return " and ".join(
            f"metadata[{json.dumps(key)}] == {json.dumps(value)}"
            for key, value in sorted(metadata_filter.items())
        )
