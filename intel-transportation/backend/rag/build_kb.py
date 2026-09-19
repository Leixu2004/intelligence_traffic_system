"""Offline ingest pipeline: load → chunk → embed (batch) → index → registry.

Idempotent per ``doc_id``: existing vectors for the document are deleted before
insertion, and the registry records ``file_sha256`` so re-ingesting an unchanged
file is detectable (PROJECT_SPEC 3.2).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .chunker import chunk_document
from .config import RagSettings
from .document_io import DocumentReadError, load_document_text
from .embedding import EmbeddingProvider
from .registry import DocumentRegistry
from .vector_store import Point, VectorStoreUnavailableError

LOGGER = logging.getLogger(__name__)
EMBED_BATCH_SIZE = 64


@dataclass
class IngestReport:
    doc_id: str
    title: str
    version: str
    file_sha256: str
    chunk_count: int
    unchanged: bool
    errors: list[str]


def _default_doc_id(path: Path) -> str:
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", path.stem).strip("_").lower()
    return (stem or f"doc_{hashlib.sha256(str(path).encode()).hexdigest()[:8]}")[:63]


def ingest_file(
    path: Path,
    settings: RagSettings,
    store,
    provider: EmbeddingProvider,
    registry: DocumentRegistry,
    *,
    doc_id: str | None = None,
    doc_version: str | None = None,
    doc_title: str | None = None,
    expiry_date: str | None = None,
) -> IngestReport:
    errors: list[str] = []
    path = Path(path)
    doc_id = (doc_id or _default_doc_id(path))[:63]
    version = (doc_version or "v1")[:31]
    try:
        text = load_document_text(path)
    except DocumentReadError as exc:
        return IngestReport(doc_id, path.name, version, "", 0, False, [str(exc)])
    if not text.strip():
        return IngestReport(doc_id, path.name, version, "", 0, False, ["文档内容为空"])

    file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    title = doc_title or path.stem
    previous = registry.get(doc_id)
    if (
        previous
        and previous.get("file_sha256") == file_sha256
        and previous.get("version") == version
        and not errors
    ):
        return IngestReport(doc_id, title, version, file_sha256, int(previous.get("chunk_count", 0)), True, errors)

    chunks = chunk_document(
        text, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )
    if not chunks:
        return IngestReport(doc_id, title, version, file_sha256, 0, False, ["切分结果为空"])

    vectors = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors.extend(provider.encode([chunk.text for chunk in batch]))
    if len(vectors) != len(chunks):
        return IngestReport(doc_id, title, version, file_sha256, 0, False, ["向量化数量不一致"])

    store.delete_document(doc_id)
    points = [
        Point(
            vector=vector,
            text=chunk.text,
            doc_id=doc_id,
            doc_version=version,
            chunk_index=chunk.chunk_index,
            section=chunk.section,
            law_article=(chunk.law_articles[0] if chunk.law_articles else ""),
            law_articles="、".join(chunk.law_articles),
            content_hash=chunk.content_hash,
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    try:
        store.upsert(points)
    except VectorStoreUnavailableError:
        raise
    except Exception as exc:
        return IngestReport(doc_id, title, version, file_sha256, 0, False, [f"向量入库失败: {exc}"])

    registry.upsert(
        doc_id=doc_id,
        title=title,
        version=version,
        source=str(path),
        file_sha256=file_sha256,
        chunk_count=len(chunks),
        expiry_date=expiry_date,
    )
    LOGGER.info("法规文档 %s (%s) 索引完成：%s 块", doc_id, version, len(chunks))
    return IngestReport(doc_id, title, version, file_sha256, len(chunks), False, errors)


def ingest_directory(
    settings: RagSettings,
    store,
    provider: EmbeddingProvider,
    registry: DocumentRegistry,
    only_doc_id: str | None = None,
) -> list[IngestReport]:
    reports: list[IngestReport] = []
    docs_dir = Path(settings.docs_dir).expanduser()
    if not docs_dir.exists():
        return [IngestReport("directory", str(docs_dir), "", "", 0, False, [f"法规目录不存在: {docs_dir}"])]
    supported = sorted(
        path
        for path in docs_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".txt", ".md", ".docx"}
    )
    for path in supported:
        doc_id = _default_doc_id(path)
        if only_doc_id and doc_id != only_doc_id:
            continue
        reports.append(ingest_file(path, settings, store, provider, registry, doc_id=doc_id))
    return reports
