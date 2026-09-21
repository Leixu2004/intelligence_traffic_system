"""集成层的对外契约（Pydantic 模型）。

响应统一成 `{code, message, data, timestamp}` 信封，与 backend/vision、backend/crew 一致，
大屏（Streamlit）只认这一种形状。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntegrationStageModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    status: str  # ok / degraded / skipped
    latency_ms: int = 0
    detail: str = ""


class IntegrationRunModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    started_at: str = ""
    pipeline: list[str] = Field(default_factory=list)
    stages: list[IntegrationStageModel] = Field(default_factory=list)
    query: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None
    push: dict[str, Any] = Field(default_factory=dict)
    degradations: list[str] = Field(default_factory=list)
    verified: bool = False
    simulation: bool = True
    latency_ms: int = 0


class IntegrationRunResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: IntegrationRunModel
    timestamp: int


class IntegrationEnvelope(BaseModel):
    """health / pushes / 报告等非结构化 data 的统一信封。"""

    code: int = 200
    message: str = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: int
