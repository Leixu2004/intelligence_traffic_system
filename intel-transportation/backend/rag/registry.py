"""Law document version registry (JSON-backed per PROJECT_SPEC Step 2)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


class DocumentRegistry:
    """Tracks ingested documents with version, hash and lifecycle status."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._documents: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._documents = {
                entry["doc_id"]: entry for entry in payload.get("documents", [])
            }
        except (json.JSONDecodeError, KeyError, OSError):
            LOGGER.exception("无法解析法规文档登记表 %s，按空登记表继续", self.path)
            self._documents = {}

    def _save(self) -> None:
        payload = {
            "documents": sorted(self._documents.values(), key=lambda item: item["doc_id"]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).date()

    def _effective_status(self, entry: dict[str, Any]) -> str:
        expiry = entry.get("expiry_date")
        if expiry:
            try:
                if date.fromisoformat(str(expiry)) < self._today():
                    return "expired"
            except ValueError:
                LOGGER.warning("登记表 %s 的 expiry_date 无效", entry.get("doc_id"))
        return str(entry.get("status", "active"))

    def upsert(
        self,
        *,
        doc_id: str,
        title: str,
        version: str,
        source: str,
        file_sha256: str,
        chunk_count: int,
        expiry_date: str | None = None,
    ) -> dict[str, Any]:
        previous = self._documents.get(doc_id)
        history = list(previous.get("history", [])) if previous else []
        if previous and previous.get("file_sha256") != file_sha256:
            history.append(
                {
                    "version": previous.get("version", ""),
                    "file_sha256": previous.get("file_sha256", ""),
                    "chunk_count": previous.get("chunk_count", 0),
                    "indexed_at": previous.get("indexed_at"),
                    "superseded_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        entry = {
            "doc_id": doc_id,
            "title": title,
            "version": version,
            "status": "active",
            "source": source,
            "file_sha256": file_sha256,
            "chunk_count": chunk_count,
            "expiry_date": expiry_date,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "history": history[-10:],
        }
        self._documents[doc_id] = entry
        self._save()
        return entry

    def get(self, doc_id: str) -> dict[str, Any] | None:
        return self._documents.get(doc_id)

    def list_documents(self) -> list[dict[str, Any]]:
        entries = []
        for entry in self._documents.values():
            item = dict(entry)
            item["effective_status"] = self._effective_status(entry)
            entries.append(item)
        return entries

    def active_doc_ids(self) -> list[str]:
        return [
            doc_id
            for doc_id, entry in self._documents.items()
            if self._effective_status(entry) == "active"
        ]

    def set_status(self, doc_id: str, status: str) -> bool:
        if doc_id not in self._documents or status not in {"active", "superseded", "expired"}:
            return False
        self._documents[doc_id]["status"] = status
        self._save()
        return True

    def clear(self) -> None:
        """Drop all registrations (used when the vector index is rebuilt)."""
        self._documents = {}
        self._save()
