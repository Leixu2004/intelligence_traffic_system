"""Law knowledge retriever: embed query → vector search → threshold filter."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import RagSettings
from .contracts import RagCitation
from .embedding import EmbeddingProvider
from .registry import DocumentRegistry

LOGGER = logging.getLogger(__name__)


class RagUnavailableError(RuntimeError):
    """Raised when the RAG subsystem cannot serve a query."""


@dataclass
class RetrievalResult:
    hits: list[tuple[RagCitation, str]]  # (citation, full text)
    searched: bool
    detail: str


class LawRetriever:
    def __init__(self, settings: RagSettings, store, provider: EmbeddingProvider,
                 registry: DocumentRegistry):
        self.settings = settings
        self.store = store
        self.provider = provider
        self.registry = registry

    def search(self, question: str, top_k: int | None = None) -> list[tuple[RagCitation, str]]:
        if self.store is None:
            raise RagUnavailableError("向量库不可用")
        limit = min(8, max(1, int(top_k or self.settings.top_k)))
        allowed = self.registry.active_doc_ids() if self.registry is not None else None
        query_vector = self.provider.encode_query(question)
        raw_hits = self.store.search(query_vector, limit=limit, allowed_doc_ids=allowed)
        results: list[tuple[RagCitation, str]] = []
        for hit in raw_hits:
            if hit.score < self.settings.min_score:
                continue
            meta = hit.metadata
            chunk_id = f"{meta.get('doc_id', '')}#{meta.get('chunk_index', 0)}"
            citation = RagCitation(
                chunk_id=chunk_id,
                doc_id=str(meta.get("doc_id", "")),
                doc_title=str(meta.get("doc_id", "")),
                doc_version=str(meta.get("doc_version", "")),
                law_articles=str(meta.get("law_articles") or meta.get("law_article") or ""),
                chunk_index=int(meta.get("chunk_index", 0)),
                section=str(meta.get("section", "")),
                score=round(float(hit.score), 4),
                snippet=hit.text[:120],
            )
            if self.registry is not None:
                entry = self.registry.get(citation.doc_id)
                if entry:
                    citation.doc_title = str(entry.get("title", citation.doc_id))
            results.append((citation, hit.text))
        return results
