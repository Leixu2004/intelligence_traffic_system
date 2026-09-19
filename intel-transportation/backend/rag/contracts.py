"""Public API contracts for the traffic law RAG subsystem."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RagQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=8)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question cannot be blank")
        return value


class RagCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    doc_id: str
    doc_title: str
    doc_version: str
    law_articles: str
    chunk_index: int
    section: str = ""
    score: float
    snippet: str


class RagQueryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    question: str
    answer: str
    refusal: bool
    generation: str  # "llm" | "retrieval-only" | "refusal"
    citations: list[RagCitation] = Field(default_factory=list)
    embedding_provider: str
    embedding_model: str
    store_backend: str
    llm_model: str
    latency_ms: float = 0.0
    limitations: list[str] = Field(default_factory=list)


class RagQueryResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: RagQueryData
    timestamp: int


class RagRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str | None = Field(default=None, max_length=64)


class RagRebuildData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    documents: list[dict[str, Any]] = Field(default_factory=list)
    total_chunks: int = 0
    store_backend: str
    errors: list[str] = Field(default_factory=list)


class RagRebuildResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: RagRebuildData
    timestamp: int
