"""RagService: lifecycle, health, query, rebuild and agent-tool surface."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .audit import JsonlAuditLog
from .build_kb import ingest_directory, ingest_file
from .config import RagSettings
from .embedding import EmbeddingUnavailableError, build_embedding_provider
from .qa import LawQaService
from .registry import DocumentRegistry
from .retriever import LawRetriever, RagUnavailableError
from .vector_store import VectorStoreUnavailableError, create_vector_store

LOGGER = logging.getLogger(__name__)


class RagService:
    """Optional RAG subsystem; initialisation failures never raise outward."""

    def __init__(self, settings: RagSettings):
        self.settings = settings
        self.audit = JsonlAuditLog(settings.audit_path)
        self.store = None
        self.provider = None
        self.registry: DocumentRegistry | None = None
        self.retriever: LawRetriever | None = None
        self.qa: LawQaService | None = None
        self._load_error = ""
        self._initialise()

    def _initialise(self) -> None:
        if not self.settings.enabled:
            self._load_error = "TRAFFIC_RAG_ENABLED 未启用"
            return
        try:
            self.provider = build_embedding_provider(self.settings)
            self.registry = DocumentRegistry(self.settings.registry_path)
            store, backend, detail = create_vector_store(
                self.settings, self.settings.embedding_dim
            )
            if store is None:
                raise VectorStoreUnavailableError(detail)
            self.store = store
            if getattr(store, "recreated", False) and self.registry is not None:
                self.registry.clear()
            self.retriever = LawRetriever(self.settings, store, self.provider, self.registry)
            self.qa = LawQaService(self.settings, self.retriever)
        except (EmbeddingUnavailableError, VectorStoreUnavailableError) as exc:
            self._load_error = str(exc)
            LOGGER.warning("法规 RAG 初始化未完成: %s", exc)
        except Exception:
            self._load_error = "法规 RAG 初始化失败"
            LOGGER.exception("法规 RAG 初始化失败")

    @property
    def available(self) -> bool:
        return self.retriever is not None and self.store is not None

    def health(self) -> dict[str, Any]:
        chunk_count = self.store.count() if self.store is not None else 0
        documents = self.registry.list_documents() if self.registry is not None else []
        return {
            "enabled": self.settings.enabled,
            "available": self.available,
            "store_backend": getattr(self.store, "backend_name", "unavailable"),
            "collection": self.settings.collection,
            "chunk_count": chunk_count,
            "document_count": len(documents),
            "documents": [
                {
                    "doc_id": entry.get("doc_id"),
                    "title": entry.get("title"),
                    "version": entry.get("version"),
                    "status": entry.get("effective_status"),
                    "chunk_count": entry.get("chunk_count"),
                }
                for entry in documents
            ],
            "embedding_provider": self.settings.embedding_provider,
            "embedding_model": self.settings.embedding_model,
            "embedding_dim": self.settings.embedding_dim,
            "llm_configured": self.settings.llm_configured,
            "llm_model": self.settings.llm_model,
            "simulation": bool(self.provider and self.provider.simulation),
            "error": self._load_error,
        }

    def query(self, question: str, top_k: int | None = None):
        if not self.available or self.qa is None:
            raise RagUnavailableError(self._load_error or "法规 RAG 不可用")
        started = time.perf_counter()
        data = self.qa.answer(question, top_k)
        self.audit.append(
            {
                "trace_id": data.trace_id,
                "kind": "rag_query",
                "question": question,
                "refusal": data.refusal,
                "generation": data.generation,
                "citations": [citation.model_dump() for citation in data.citations],
                "embedding_provider": data.embedding_provider,
                "store_backend": data.store_backend,
                "answer": data.answer,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return data

    def search_only(self, question: str, top_k: int = 3) -> dict[str, Any]:
        """Read-only search surface for the traffic agent toolbox."""

        if not self.available or self.retriever is None:
            return {
                "ok": False,
                "message": self._load_error or "法规知识库不可用",
                "source": "LawRAG",
            }
        try:
            hits = self.retriever.search(question, top_k)
        except RagUnavailableError as exc:
            return {"ok": False, "message": str(exc), "source": "LawRAG"}
        except Exception as exc:
            LOGGER.exception("法规检索失败")
            return {"ok": False, "message": f"法规检索失败: {type(exc).__name__}", "source": "LawRAG"}
        if not hits:
            return {
                "ok": True,
                "found": False,
                "message": "知识库中没有与问题相关的条文，请如实告知用户无法回答",
                "citations": [],
                "source": "LawRAG",
                "simulation": bool(self.provider and self.provider.simulation),
            }
        payload = []
        for citation, text in hits:
            payload.append(
                {
                    "law_articles": citation.law_articles or "未标注条号",
                    "doc_title": citation.doc_title,
                    "doc_version": citation.doc_version,
                    "chunk_id": citation.chunk_id,
                    "score": citation.score,
                    "text": text[:600],
                }
            )
        return {
            "ok": True,
            "found": True,
            "citations": payload,
            "note": "条文来自版本化法规知识库，回答必须引用条号与来源；知识库未覆盖的问题应如实拒答",
            "source": "LawRAG",
            "simulation": bool(self.provider and self.provider.simulation),
        }

    def rebuild(self, only_doc_id: str | None = None) -> dict[str, Any]:
        if not self.available or self.provider is None:
            raise RagUnavailableError(self._load_error or "法规 RAG 不可用")
        trace_id = uuid4().hex
        reports = ingest_directory(
            self.settings, self.store, self.provider, self.registry, only_doc_id=only_doc_id
        )
        documents = []
        errors: list[str] = []
        total_chunks = 0
        for report in reports:
            documents.append(
                {
                    "doc_id": report.doc_id,
                    "title": report.title,
                    "version": report.version,
                    "chunk_count": report.chunk_count,
                    "unchanged": report.unchanged,
                }
            )
            total_chunks += report.chunk_count
            errors.extend(report.errors)
        result = {
            "trace_id": trace_id,
            "documents": documents,
            "total_chunks": total_chunks,
            "store_backend": getattr(self.store, "backend_name", "unknown"),
            "errors": errors,
        }
        self.audit.append({"trace_id": trace_id, "kind": "rag_rebuild", **result})
        return result

    def ingest_path(self, path, **kwargs) -> dict[str, Any]:
        if not self.available or self.provider is None:
            raise RagUnavailableError(self._load_error or "法规 RAG 不可用")
        report = ingest_file(
            Path(path) if not isinstance(path, Path) else path,
            self.settings,
            self.store,
            self.provider,
            self.registry,
            **kwargs,
        )
        return {
            "doc_id": report.doc_id,
            "chunk_count": report.chunk_count,
            "unchanged": report.unchanged,
            "errors": report.errors,
        }

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
