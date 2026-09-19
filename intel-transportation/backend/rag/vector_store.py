"""Vector store backends: Milvus Lite (spec primary) and FAISS (fallback).

Both backends persist to disk, support deletion by ``doc_id`` for idempotent
re-ingest, and return cosine similarity scores (higher is better).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

LOGGER = logging.getLogger(__name__)

COLLECTION_TEXT_MAX = 65535
ARTICLE_FIELD_MAX = 64
ARTICLES_FIELD_MAX = 512


class VectorStoreUnavailableError(RuntimeError):
    """Raised when no vector backend can be initialised."""


@dataclass
class SearchHit:
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Point:
    vector: Sequence[float]
    text: str
    doc_id: str
    doc_version: str
    chunk_index: int
    section: str
    law_article: str
    law_articles: str
    content_hash: str


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MilvusLiteStore:
    backend_name = "milvus-lite"

    def __init__(self, uri: Path, collection: str, dim: int):
        try:
            from pymilvus import DataType, MilvusClient
        except ImportError as exc:
            raise VectorStoreUnavailableError(
                "pymilvus 未安装，无法使用 Milvus Lite 后端"
            ) from exc
        uri = Path(uri).expanduser()
        uri.parent.mkdir(parents=True, exist_ok=True)
        self._DataType = DataType
        self.recreated = False
        try:
            self._client = MilvusClient(uri=str(uri))
        except Exception as exc:
            raise VectorStoreUnavailableError(
                f"Milvus Lite 初始化失败: {exc}"
            ) from exc
        self.collection = collection
        self.dim = dim
        self._ensure_collection()

    def _stored_dim(self) -> int | None:
        try:
            description = self._client.describe_collection(self.collection)
        except Exception:
            return None
        for field_def in description.get("fields", []):
            if field_def.get("name") == "vector":
                params = field_def.get("params") or {}
                try:
                    return int(params.get("dim", 0))
                except (TypeError, ValueError):
                    return None
        return None

    def _ensure_collection(self) -> None:
        if self._client.has_collection(self.collection):
            stored_dim = self._stored_dim()
            if stored_dim is not None and stored_dim != self.dim:
                LOGGER.warning(
                    "Milvus 集合维度 %s 与配置维度 %s 不一致，重建集合并等待重新摄取",
                    stored_dim,
                    self.dim,
                )
                self._client.drop_collection(self.collection)
                self.recreated = True
            else:
                try:
                    self._client.load_collection(self.collection)
                except Exception:
                    LOGGER.debug("Milvus collection load skipped", exc_info=True)
                return
        schema = self._client.create_schema(auto_id=True)
        schema.add_field("id", self._DataType.INT64, is_primary=True)
        schema.add_field("vector", self._DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("text", self._DataType.VARCHAR, max_length=COLLECTION_TEXT_MAX)
        schema.add_field("doc_id", self._DataType.VARCHAR, max_length=64)
        schema.add_field("doc_version", self._DataType.VARCHAR, max_length=32)
        schema.add_field("chunk_index", self._DataType.INT32)
        schema.add_field("section", self._DataType.VARCHAR, max_length=128)
        schema.add_field("law_article", self._DataType.VARCHAR, max_length=ARTICLE_FIELD_MAX)
        schema.add_field("law_articles", self._DataType.VARCHAR, max_length=ARTICLES_FIELD_MAX)
        schema.add_field("content_hash", self._DataType.VARCHAR, max_length=64)
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 256},
        )
        self._client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=index_params,
        )

    def _row(self, point: Point) -> dict[str, Any]:
        return {
            "vector": [float(value) for value in point.vector],
            "text": point.text[:COLLECTION_TEXT_MAX - 1],
            "doc_id": point.doc_id[:63],
            "doc_version": point.doc_version[:31],
            "chunk_index": int(point.chunk_index),
            "section": point.section[:127],
            "law_article": point.law_article[:ARTICLE_FIELD_MAX - 1],
            "law_articles": point.law_articles[:ARTICLES_FIELD_MAX - 1],
            "content_hash": point.content_hash[:63],
        }

    def upsert(self, points: Sequence[Point]) -> int:
        if not points:
            return 0
        rows = [self._row(point) for point in points]
        for start in range(0, len(rows), 64):
            self._client.insert(collection_name=self.collection, data=rows[start : start + 64])
        return len(rows)

    def delete_document(self, doc_id: str) -> None:
        self._client.delete(
            collection_name=self.collection, filter=f'doc_id == "{_escape(doc_id)}"'
        )

    def count(self) -> int:
        try:
            stats = self._client.get_collection_stats(self.collection)
            return int(stats.get("row_count", 0))
        except Exception:
            return 0

    def search(
        self,
        vector: Sequence[float],
        limit: int,
        allowed_doc_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        search_params: dict[str, Any] = {
            "collection_name": self.collection,
            "data": [[float(value) for value in vector]],
            "limit": max(limit * 3, limit),
            "output_fields": [
                "text",
                "doc_id",
                "doc_version",
                "chunk_index",
                "section",
                "law_article",
                "law_articles",
                "content_hash",
            ],
        }
        if allowed_doc_ids is not None:
            if not allowed_doc_ids:
                return []
            values = ", ".join(f'"{_escape(doc_id)}"' for doc_id in allowed_doc_ids)
            search_params["filter"] = f"doc_id in [{values}]"
        results = self._client.search(**search_params)
        hits: list[SearchHit] = []
        for row in results[0]:
            entity = row.get("entity", {})
            hits.append(
                SearchHit(
                    score=float(row.get("distance", 0.0)),
                    text=str(entity.get("text", "")),
                    metadata={
                        "doc_id": entity.get("doc_id", ""),
                        "doc_version": entity.get("doc_version", ""),
                        "chunk_index": entity.get("chunk_index", 0),
                        "section": entity.get("section", ""),
                        "law_article": entity.get("law_article", ""),
                        "law_articles": entity.get("law_articles", ""),
                        "content_hash": entity.get("content_hash", ""),
                    },
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            LOGGER.debug("Milvus Lite client close failed", exc_info=True)


class FaissStore:
    """FAISS fallback: flat inner-product index over normalized vectors."""

    backend_name = "faiss"

    def __init__(self, directory: Path, collection: str, dim: int):
        try:
            import faiss
        except ImportError as exc:
            raise VectorStoreUnavailableError(
                "faiss-cpu 未安装，无法使用 FAISS 后端"
            ) from exc
        self._faiss = faiss
        self.directory = Path(directory).expanduser()
        self.collection = collection
        self.dim = dim
        self.directory.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.directory / f"{collection}_meta.json"
        self._index_path = self.directory / f"{collection}_index.faiss"
        self._ids: list[str] = []
        self._records: list[dict[str, Any]] = []
        self._index = None
        self._load()

    def _load(self) -> None:
        if self._meta_path.exists():
            payload = json.loads(self._meta_path.read_text(encoding="utf-8"))
            self._records = payload.get("records", [])
            self._ids = payload.get("ids", [])
            self._index = self._faiss.read_index(str(self._index_path))
        else:
            self._index = self._faiss.IndexFlatIP(self.dim)

    def _save(self) -> None:
        self._faiss.write_index(self._index, str(self._index_path))
        self._meta_path.write_text(
            json.dumps({"ids": self._ids, "records": self._records}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _vector(self, point: Point) -> np.ndarray:
        vector = np.asarray([list(point.vector)], dtype="float32")
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector = vector / norm
        return vector

    def upsert(self, points: Sequence[Point]) -> int:
        for point in points:
            self._index.add(self._vector(point))
            self._ids.append(f"{point.doc_id}#{point.chunk_index}")
            self._records.append(
                {
                    "text": point.text,
                    "doc_id": point.doc_id,
                    "doc_version": point.doc_version,
                    "chunk_index": point.chunk_index,
                    "section": point.section,
                    "law_article": point.law_article,
                    "law_articles": point.law_articles,
                    "content_hash": point.content_hash,
                }
            )
        self._save()
        return len(points)

    def delete_document(self, doc_id: str) -> None:
        keep = [
            (vector_id, record)
            for vector_id, record in zip(self._ids, self._records, strict=True)
            if record.get("doc_id") != doc_id
        ]
        if len(keep) == len(self._ids):
            return
        vectors = (
            self._index.reconstruct_n(0, self._index.ntotal)
            if self._index.ntotal
            else np.zeros((0, self.dim), dtype="float32")
        )
        keep_positions = {
            position
            for position, (vector_id, _) in enumerate(zip(self._ids, self._records, strict=True))
            if self._records[position].get("doc_id") != doc_id
        }
        self._ids = [vector_id for vector_id, _ in keep]
        self._records = [record for _, record in keep]
        self._index = self._faiss.IndexFlatIP(self.dim)
        if len(keep_positions):
            survivor = vectors[sorted(keep_positions)]
            self._index.add(survivor)
        self._save()

    def count(self) -> int:
        return int(self._index.ntotal)

    def search(
        self,
        vector: Sequence[float],
        limit: int,
        allowed_doc_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        if not self._index.ntotal:
            return []
        query = np.asarray([list(vector)], dtype="float32")
        norm = float(np.linalg.norm(query))
        if norm > 0:
            query = query / norm
        scores, positions = self._index.search(query, min(self._index.ntotal, limit * 3))
        hits: list[SearchHit] = []
        for score, position in zip(scores[0], positions[0], strict=False):
            if position < 0 or position >= len(self._records):
                continue
            record = self._records[position]
            if allowed_doc_ids is not None and record.get("doc_id") not in allowed_doc_ids:
                continue
            hits.append(
                SearchHit(
                    score=float(score),
                    text=str(record.get("text", "")),
                    metadata={key: record.get(key, "") for key in (
                        "doc_id",
                        "doc_version",
                        "chunk_index",
                        "section",
                        "law_article",
                        "law_articles",
                        "content_hash",
                    )},
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def close(self) -> None:
        return None


def create_vector_store(settings, dim: int) -> tuple[Any | None, str, str]:
    """Create the configured vector store.

    Returns ``(store, backend_name, detail)``. ``store`` is ``None`` when no
    backend could be initialised; ``detail`` explains why for health reporting.
    In ``auto`` mode Milvus Lite is tried first and FAISS is the explicit
    fallback; an explicitly configured backend never silently degrades.
    """

    errors: list[str] = []
    requested = [settings.vector_backend]
    if settings.vector_backend == "auto":
        requested = ["milvus-lite", "faiss"]
    for backend in requested:
        try:
            if backend == "milvus-lite":
                return (
                    MilvusLiteStore(settings.milvus_uri, settings.collection, dim),
                    "milvus-lite",
                    "",
                )
            if backend == "faiss":
                directory = settings.milvus_uri.parent / f"faiss_{settings.collection}"
                return (
                    FaissStore(directory, settings.collection, dim),
                    "faiss",
                    "",
                )
        except VectorStoreUnavailableError as exc:
            errors.append(f"{backend}: {exc}")
    return (None, "unavailable", "; ".join(errors) or "未配置可用后端")
