"""Pydantic contracts for the emergency crew."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EmergencyEvent(BaseModel):
    """一次应急事件输入，对应课件第 11 页的输入事件结构。"""

    event_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    occurred_at: str | None = Field(default=None, max_length=40)
    location_text: str = Field(default="", max_length=200)
    checkpoint_id: str | None = Field(default=None, max_length=64)
    origin_gps: tuple[float, float] | None = Field(
        default=None, description="事故点 [经度, 纬度]，缺省时用卡口配置坐标"
    )
    destination_gps: tuple[float, float] | None = Field(
        default=None, description="绕行目的地 [经度, 纬度]，缺省时由备选走廊拓扑决定"
    )
    severity_hint: Literal["", "I", "II", "III", "IV"] = ""
    lanes_blocked: int = Field(default=0, ge=0, le=12)


class CrewRunResult(BaseModel):
    ok: bool
    process: str
    event: EmergencyEvent
    report: str = ""
    tool_evidence: list[dict[str, Any]] = Field(default_factory=list)
    degradation: str | None = None
    elapsed_ms: float = 0.0
    simulation: bool = False
    limitations: list[str] = Field(default_factory=list)


class CrewRunResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: CrewRunResult
    timestamp: int


class RoutePlanRequest(BaseModel):
    """路径诱导入参，与 EmergencyEvent 沿用同一套经纬度字段写法。"""

    origin_gps: tuple[float, float] = Field(description="起点 [经度, 纬度]")
    destination_gps: tuple[float, float] | None = Field(
        default=None, description="终点 [经度, 纬度]，缺省时由备选走廊拓扑决定"
    )


class RoutePlanResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: dict[str, Any]
    timestamp: int
