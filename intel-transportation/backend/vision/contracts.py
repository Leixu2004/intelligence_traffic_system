"""多模态子系统的对外契约（Pydantic 模型）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VisionQuestionRequest(BaseModel):
    """VQA 请求：对已分析过的图片追问。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=300)


class VisionFrameResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int = 0
    time: str = ""
    timestamp_seconds: float = 0.0
    vehicles: int | None = None
    vehicle_source: str = "unavailable"
    description: str = ""
    status: str = "unknown"
    accident: dict[str, Any] | None = None
    error: str = ""


class VisionAnalysisData(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = ""
    model: str = ""
    extraction: dict[str, Any] = Field(default_factory=dict)
    timeline: list[VisionFrameResult] = Field(default_factory=list)
    narration: str = ""
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    degradations: list[str] = Field(default_factory=list)
    accident: bool = False
    verified: bool = False
    elapsed_ms: int = 0


class VisionAnalysisResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: VisionAnalysisData
    timestamp: int


class VisionRunsResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: dict[str, Any]
    timestamp: int
