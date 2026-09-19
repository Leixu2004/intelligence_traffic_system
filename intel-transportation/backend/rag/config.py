"""Environment-driven configuration for the traffic law RAG subsystem.

Credentials deliberately fall back to the traffic assistant (``TRAFFIC_AGENT_*``)
chain so the RAG service and the agent share one LLM configuration source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

INNER_ROOT = Path(__file__).resolve().parents[2]
BAILIAN_PROVIDERS = {"aliyun-bailian", "bailian", "dashscope"}
BAILIAN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_DEFAULT_MODEL = "deepseek-v4-flash-0731"

EMBEDDING_PROVIDERS = {"bge-local", "openai-compatible", "hashing-test"}
VECTOR_BACKENDS = {"auto", "milvus-lite", "faiss"}
DOCUMENT_STATUSES = {"active", "superseded", "expired"}


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _number(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class RagSettings:
    enabled: bool
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    embedding_device: str
    vector_backend: str
    milvus_uri: Path
    collection: str
    docs_dir: Path
    registry_path: Path
    audit_path: Path
    top_k: int
    min_score: float
    chunk_size: int
    chunk_overlap: int
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str | None
    llm_temperature: float
    llm_timeout_seconds: float
    llm_max_retries: int

    @property
    def embedding_configured(self) -> bool:
        if self.embedding_provider == "hashing-test":
            return True
        if self.embedding_provider == "bge-local":
            return bool(self.embedding_model)
        return bool(self.embedding_model and self.llm_api_key)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)


def load_rag_settings() -> RagSettings:
    provider = (
        os.getenv("TRAFFIC_RAG_EMBEDDING_PROVIDER", "bge-local").strip().lower()
        or "bge-local"
    )
    if provider not in EMBEDDING_PROVIDERS:
        provider = "bge-local"
    backend = os.getenv("TRAFFIC_RAG_VECTOR_BACKEND", "auto").strip().lower() or "auto"
    if backend not in VECTOR_BACKENDS:
        backend = "auto"

    llm_api_key = (
        os.getenv("TRAFFIC_RAG_LLM_API_KEY", "").strip()
        or os.getenv("TRAFFIC_AGENT_API_KEY", "").strip()
        or os.getenv("DASHSCOPE_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    llm_provider = (
        os.getenv("TRAFFIC_RAG_LLM_PROVIDER", "").strip().lower()
        or os.getenv("TRAFFIC_AGENT_PROVIDER", "").strip().lower()
        or "aliyun-bailian"
    )
    llm_base_url = (
        os.getenv("TRAFFIC_RAG_LLM_BASE_URL", "").strip()
        or os.getenv("TRAFFIC_AGENT_BASE_URL", "").strip()
    ) or None
    if llm_base_url is None and llm_provider in BAILIAN_PROVIDERS:
        llm_base_url = BAILIAN_DEFAULT_BASE_URL
    llm_model = (
        os.getenv("TRAFFIC_RAG_LLM_MODEL", "").strip()
        or os.getenv("TRAFFIC_AGENT_MODEL", "").strip()
        or (BAILIAN_DEFAULT_MODEL if llm_provider in BAILIAN_PROVIDERS else "gpt-4o-mini")
    )

    embedding_model = os.getenv("TRAFFIC_RAG_EMBEDDING_MODEL", "").strip()
    if not embedding_model:
        if provider == "hashing-test":
            embedding_model = "hashing-ngram-test"
        elif provider == "openai-compatible":
            embedding_model = "text-embedding-v4"
        else:
            embedding_model = "BAAI/bge-large-zh-v1.5"

    rag_root = INNER_ROOT / "data" / "rag"
    return RagSettings(
        enabled=_flag("TRAFFIC_RAG_ENABLED", default=False),
        embedding_provider=provider,
        embedding_model=embedding_model,
        embedding_dim=_integer("TRAFFIC_RAG_EMBEDDING_DIM", 1024, 64, 4096),
        embedding_device=os.getenv("TRAFFIC_RAG_EMBEDDING_DEVICE", "cpu").strip()
        or "cpu",
        vector_backend=backend,
        milvus_uri=Path(
            os.getenv("TRAFFIC_RAG_MILVUS_URI", str(rag_root / "traffic_law.db"))
        ).expanduser(),
        collection=os.getenv("TRAFFIC_RAG_COLLECTION", "traffic_law").strip()
        or "traffic_law",
        docs_dir=Path(
            os.getenv("TRAFFIC_RAG_DOCS_DIR", str(rag_root / "laws"))
        ).expanduser(),
        registry_path=Path(
            os.getenv(
                "TRAFFIC_RAG_REGISTRY_PATH", str(rag_root / "law_documents.json")
            )
        ).expanduser(),
        audit_path=Path(
            os.getenv("TRAFFIC_RAG_AUDIT_PATH", str(rag_root / "rag_runs.jsonl"))
        ).expanduser(),
        top_k=_integer("TRAFFIC_RAG_TOP_K", 3, 1, 8),
        min_score=_number("TRAFFIC_RAG_MIN_SCORE", 0.5, 0.0, 1.0),
        chunk_size=_integer("TRAFFIC_RAG_CHUNK_SIZE", 500, 100, 2000),
        chunk_overlap=_integer("TRAFFIC_RAG_CHUNK_OVERLAP", 50, 0, 500),
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_temperature=_number("TRAFFIC_RAG_TEMPERATURE", 0.1, 0.0, 1.0),
        llm_timeout_seconds=_number("TRAFFIC_RAG_LLM_TIMEOUT_SECONDS", 30.0, 3.0, 120.0),
        llm_max_retries=_integer("TRAFFIC_RAG_LLM_MAX_RETRIES", 2, 0, 5),
    )
