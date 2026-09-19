"""Public API contracts for the traffic assistant."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssistantQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    thread_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    checkpoint_id: str | None = Field(default=None, max_length=64)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question cannot be blank")
        return value

    @field_validator("checkpoint_id")
    @classmethod
    def strip_checkpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ToolEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any]
    summary: str
    source: str
    ok: bool = True
    elapsed_ms: float = 0.0


class AssistantData(BaseModel):
    trace_id: str
    thread_id: str
    answer: str
    provider: str
    model: str
    tool_evidence: list[ToolEvidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    simulation: bool = False


class AssistantResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: AssistantData
    timestamp: int
