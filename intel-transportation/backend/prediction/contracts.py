"""Pydantic contracts for the prediction API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    features: list[Any] = Field(
        ...,
        min_length=1,
        description=(
            "历史特征。单变量模型接受 1×T、T×1 或 B×T；多变量 LSTM 接受 "
            "T×F 或 B×T×F，特征顺序以 /model/info 的 feature_columns 为准"
        ),
    )
    future_steps: int | None = Field(
        None, ge=1, le=15, description="预测未来步数；省略时使用当前模型上限"
    )
    moving_average_window: int = Field(5, ge=2, le=60, description="移动平均窗口")
    time_step_seconds: int | None = Field(
        None, ge=1, le=3600, description="时间步长；省略时使用当前模型粒度"
    )
    target_column: Literal["vehicle_count", "entering_vehicle_count"] = Field(
        "vehicle_count", description="输入序列的计数口径"
    )
    checkpoint_id: str | None = Field(None, description="可选卡口编号")


class CheckpointPredictRequest(BaseModel):
    checkpoint_id: str = Field(..., min_length=1, max_length=64, description="卡口编号")
    history_points: int = Field(20, ge=2, le=1440, description="读取的分钟级历史点数")
    future_steps: int | None = Field(
        None, ge=1, le=15, description="预测未来步数；省略时使用当前模型上限"
    )
    moving_average_window: int = Field(5, ge=2, le=60, description="移动平均窗口")


class PredictionData(BaseModel):
    flow: float
    forecast: list[float]
    flows: list[float] = Field(default_factory=list)
    forecasts: list[list[float]] = Field(default_factory=list)
    unit: str
    model: str
    backend: str
    latency_ms: float
    checkpoint_id: str | None = None
    target_column: str = "vehicle_count"


class PredictionResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: PredictionData
    timestamp: int
