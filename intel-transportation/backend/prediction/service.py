"""Prediction business service with ONNX Runtime and deterministic fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .holiday_calendar import china_prediction_calendar_metadata

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover - optional until backend dependencies are installed
    ort = None


MODEL_NAME = "location1_trend_v1"
FORECAST_POINTS = 16


@dataclass
class PredictionResult:
    current_flow: float
    forecast: list[float]
    current_flows: list[float]
    forecasts: list[list[float]]
    latency_ms: float
    backend: str
    model_name: str
    target_column: str


def _parse_calendar_period(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("inference_calendar_periods 的每一项必须是对象")
    valid_from = date.fromisoformat(str(raw.get("valid_from")))
    valid_to = date.fromisoformat(str(raw.get("valid_to")))
    if valid_from > valid_to:
        raise ValueError("日历周期 valid_from 不能晚于 valid_to")
    holiday_dates = {
        date.fromisoformat(str(value)).isoformat()
        for value in raw.get("holiday_dates", [])
    }
    makeup_workdays = {
        date.fromisoformat(str(value)).isoformat()
        for value in raw.get("makeup_workdays", [])
    }
    if holiday_dates & makeup_workdays:
        raise ValueError("日历周期内 holiday_dates 与 makeup_workdays 不能重叠")
    for value in holiday_dates | makeup_workdays:
        parsed = date.fromisoformat(value)
        if parsed < valid_from or parsed > valid_to:
            raise ValueError(f"日历日期 {value} 超出周期范围")
    return {
        "calendar_policy_id": str(raw.get("calendar_policy_id") or "unspecified"),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source_url": str(raw.get("source_url") or ""),
        "source_title": str(raw.get("source_title") or ""),
        "document_no": str(raw.get("document_no") or ""),
        "published_date": str(raw.get("published_date") or ""),
        "holiday_feature_semantics": str(raw.get("holiday_feature_semantics") or ""),
        "holiday_dates": holiday_dates,
        "makeup_workdays": makeup_workdays,
    }


def _calendar_period_info(period: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: value
            for key, value in period.items()
            if key not in {"valid_from", "valid_to", "holiday_dates", "makeup_workdays"}
            and value
        },
        "valid_from": period["valid_from"].isoformat(),
        "valid_to": period["valid_to"].isoformat(),
        "holiday_dates": sorted(period["holiday_dates"]),
        "makeup_workdays": sorted(period["makeup_workdays"]),
    }


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    if values.size < window:
        return values.astype(np.float32, copy=True)
    valid = np.convolve(values, np.ones(window, dtype=np.float32) / window, mode="valid")
    prefix = np.full(window - 1, values[0], dtype=np.float32)
    return np.concatenate([prefix, valid.astype(np.float32)])


def _normalise_batch(features: list[Any], feature_count: int = 1) -> np.ndarray:
    """Normalize accepted API shapes to ``[batch, time, feature]``."""

    if feature_count < 1:
        raise ValueError("feature_count must be a positive integer")
    array = np.asarray(features, dtype=np.float32)
    if feature_count == 1 and array.ndim == 1:
        batch = array[None, :]
        result = batch[:, :, None]
    elif feature_count == 1 and array.ndim == 2:
        if array.shape[0] == 1:
            batch = array
        elif array.shape[1] == 1:
            batch = array[:, 0][None, :]
        else:
            batch = array
        result = batch[:, :, None]
    elif array.ndim == 2 and array.shape[1] == feature_count:
        result = array[None, :, :]
    elif array.ndim == 3 and array.shape[2] == feature_count:
        result = array
    else:
        raise ValueError(
            f"features must have shape [time, {feature_count}] or "
            f"[batch, time, {feature_count}]"
        )

    if result.shape[1] < 2:
        raise ValueError("each sequence must contain at least 2 time points")
    if not np.isfinite(result).all():
        raise ValueError("features must contain finite numbers")
    if np.any(result < 0):
        raise ValueError("traffic flow values cannot be negative")
    return result.astype(np.float32, copy=False)


def _native_forecast(smoothed: np.ndarray, points: int = FORECAST_POINTS) -> np.ndarray:
    values = smoothed[:, :, 0]
    time_index = np.arange(values.shape[1], dtype=np.float32)
    centered_time = time_index - time_index.mean()
    denominator = max(float(np.dot(centered_time, centered_time)), 1e-6)
    slopes = ((values - values.mean(axis=1, keepdims=True)) * centered_time).sum(axis=1) / denominator
    offsets = np.arange(points, dtype=np.float32)
    result = values[:, -1:,] + slopes[:, None] * offsets[None, :]
    return np.maximum(result, 0.0).astype(np.float32)


class PredictionService:
    """Loads one inference session and reuses it for all requests."""

    def __init__(self, model_path: Path, intra_threads: int = 1, inter_threads: int = 1) -> None:
        self.model_path = model_path
        self.intra_threads = intra_threads
        self.inter_threads = inter_threads
        self.session: Any = None
        self.input_name = "features"
        self.output_name = "forecast"
        self.backend = "numpy-fallback"
        self.load_error = ""
        self.model_name = MODEL_NAME
        self.model_type = "trend"
        self.model_profile = ""
        self.deployment_stage = ""
        self.intended_use = ""
        self.scaler_mean = 0.0
        self.scaler_std = 1.0
        self.feature_means = np.asarray([0.0], dtype=np.float32)
        self.feature_stds = np.asarray([1.0], dtype=np.float32)
        self.feature_columns = ["vehicle_count"]
        self.target_feature_index = 0
        self.output_includes_current = True
        self.timezone = "UTC"
        self.holiday_policy = "none"
        self.holiday_calendar_source: str | None = None
        self.holiday_calendar_years: list[int] = []
        self.holiday_calendar_valid_from: date | None = None
        self.holiday_calendar_valid_to: date | None = None
        self.holiday_dates: set[str] = set()
        self.makeup_workdays: set[str] = set()
        self.inference_calendar_years: list[int] = []
        self.inference_calendar_periods: list[dict[str, Any]] = []
        self.unknown_year_policy = "reject"
        self.max_future_steps = FORECAST_POINTS - 1
        self.history_steps: int | None = None
        self.bin_seconds = 60
        self.target_column = "vehicle_count"
        self.missing_bucket_policy = "zero_fill"

    def load(self) -> None:
        metadata_path = self.model_path.with_suffix(".json")
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.model_name = str(metadata.get("model") or self.model_path.stem)
                self.model_type = str(metadata.get("model_type") or "trend")
                self.model_profile = str(metadata.get("profile") or "")
                self.deployment_stage = str(metadata.get("deployment_stage") or "")
                self.intended_use = str(metadata.get("intended_use") or "")
                self.scaler_mean = float(metadata.get("mean", 0.0))
                self.scaler_std = max(float(metadata.get("std", 1.0)), 1e-6)
                raw_feature_columns = metadata.get("feature_columns")
                if raw_feature_columns is None:
                    self.feature_columns = [str(metadata.get("target_column") or "vehicle_count")]
                elif not isinstance(raw_feature_columns, list) or not raw_feature_columns:
                    raise ValueError("feature_columns 必须是非空数组")
                else:
                    self.feature_columns = [str(column) for column in raw_feature_columns]
                raw_feature_means = metadata.get(
                    "feature_offsets",
                    metadata.get("feature_means", [self.scaler_mean]),
                )
                raw_feature_stds = metadata.get(
                    "feature_scales",
                    metadata.get("feature_stds", [self.scaler_std]),
                )
                self.feature_means = np.asarray(raw_feature_means, dtype=np.float32).reshape(-1)
                self.feature_stds = np.maximum(
                    np.asarray(raw_feature_stds, dtype=np.float32).reshape(-1), 1e-6
                )
                if (
                    len(self.feature_means) != len(self.feature_columns)
                    or len(self.feature_stds) != len(self.feature_columns)
                ):
                    raise ValueError("feature scaler 与 feature_columns 数量不一致")
                self.max_future_steps = int(metadata.get("forecast_steps", FORECAST_POINTS - 1))
                raw_history_steps = metadata.get("history_steps")
                self.history_steps = int(raw_history_steps) if raw_history_steps is not None else None
                self.bin_seconds = int(metadata.get("bin_seconds", 60))
                self.target_column = str(metadata.get("target_column") or "vehicle_count")
                self.missing_bucket_policy = str(
                    metadata.get("missing_bucket_policy") or "zero_fill"
                )
                self.output_includes_current = bool(
                    metadata.get("output_includes_current", True)
                )
                self.timezone = str(metadata.get("timezone") or "UTC")
                self.holiday_policy = str(metadata.get("holiday_policy") or "none")
                raw_calendar_source = metadata.get("holiday_calendar_source")
                self.holiday_calendar_source = (
                    str(raw_calendar_source) if raw_calendar_source else None
                )
                self.holiday_calendar_years = [
                    int(value) for value in metadata.get("holiday_calendar_years", [])
                ]
                raw_valid_from = metadata.get("holiday_calendar_valid_from")
                raw_valid_to = metadata.get("holiday_calendar_valid_to")
                self.holiday_calendar_valid_from = (
                    date.fromisoformat(str(raw_valid_from)) if raw_valid_from else None
                )
                self.holiday_calendar_valid_to = (
                    date.fromisoformat(str(raw_valid_to)) if raw_valid_to else None
                )
                self.holiday_dates = {
                    date.fromisoformat(str(value)).isoformat()
                    for value in metadata.get("holiday_dates", [])
                }
                self.makeup_workdays = {
                    date.fromisoformat(str(value)).isoformat()
                    for value in metadata.get("makeup_workdays", [])
                }
                if self.holiday_dates & self.makeup_workdays:
                    raise ValueError("holiday_dates 与 makeup_workdays 不能重叠")
                runtime_calendar = (
                    china_prediction_calendar_metadata()
                    if self.model_profile == "china_kdd2017"
                    else {}
                )
                raw_periods = metadata.get(
                    "inference_calendar_periods",
                    runtime_calendar.get("inference_calendar_periods"),
                )
                if raw_periods is not None:
                    if not isinstance(raw_periods, list) or not raw_periods:
                        raise ValueError("inference_calendar_periods 必须是非空数组")
                    self.inference_calendar_periods = [
                        _parse_calendar_period(period) for period in raw_periods
                    ]
                elif (
                    self.holiday_policy == "china_statutory_with_makeup"
                    and self.holiday_calendar_valid_from is not None
                    and self.holiday_calendar_valid_to is not None
                ):
                    self.inference_calendar_periods = [
                        {
                            "calendar_policy_id": "legacy_model_metadata",
                            "valid_from": self.holiday_calendar_valid_from,
                            "valid_to": self.holiday_calendar_valid_to,
                            "source_url": self.holiday_calendar_source or "",
                            "source_title": "",
                            "document_no": "",
                            "published_date": "",
                            "holiday_feature_semantics": "",
                            "holiday_dates": set(self.holiday_dates),
                            "makeup_workdays": set(self.makeup_workdays),
                        }
                    ]
                self.inference_calendar_periods.sort(key=lambda item: item["valid_from"])
                for previous, current in zip(
                    self.inference_calendar_periods,
                    self.inference_calendar_periods[1:],
                ):
                    if current["valid_from"] <= previous["valid_to"]:
                        raise ValueError("inference_calendar_periods 不能重叠")
                self.inference_calendar_years = [
                    int(value)
                    for value in metadata.get(
                        "inference_calendar_years",
                        runtime_calendar.get(
                            "inference_calendar_years",
                            sorted(
                                {
                                    period["valid_from"].year
                                    for period in self.inference_calendar_periods
                                }
                            ),
                        ),
                    )
                ]
                self.unknown_year_policy = str(
                    metadata.get(
                        "unknown_year_policy",
                        runtime_calendar.get("unknown_year_policy", "reject"),
                    )
                )
                if (
                    self.holiday_policy == "china_statutory_with_makeup"
                    and not self.inference_calendar_periods
                ):
                    raise ValueError("中国节假日 metadata 缺少可用的推理日历周期")
                try:
                    self.target_feature_index = self.feature_columns.index(self.target_column)
                except ValueError as exc:
                    raise ValueError("target_column 不在 feature_columns 中") from exc
                self.scaler_mean = float(
                    metadata.get(
                        "target_offset",
                        metadata.get(
                            "target_mean",
                            self.feature_means[self.target_feature_index],
                        ),
                    )
                )
                self.scaler_std = max(
                    float(
                        metadata.get(
                            "target_scale",
                            metadata.get(
                                "target_std",
                                self.feature_stds[self.target_feature_index],
                            ),
                        )
                    ),
                    1e-6,
                )
                if self.model_type == "lstm" and (self.history_steps is None or self.history_steps < 1):
                    raise ValueError("LSTM metadata 缺少有效的 history_steps")
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.load_error = f"模型元数据无效：{exc}"
                return
        if ort is None:
            self.load_error = self.load_error or "onnxruntime 未安装，使用 NumPy 兼容推理"
            return
        if not self.model_path.exists():
            self.load_error = self.load_error or f"模型文件不存在：{self.model_path}"
            return

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.intra_threads
        options.inter_op_num_threads = self.inter_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session = ort.InferenceSession(
                str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.input_name = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name
            if self.model_type == "lstm":
                input_shape = self.session.get_inputs()[0].shape
                output_shape = self.session.get_outputs()[0].shape
                expected_output_width = self.max_future_steps + int(self.output_includes_current)
                if len(input_shape) != 3 or len(output_shape) != 2:
                    raise ValueError("LSTM ONNX 必须使用三维输入和二维输出")
                if isinstance(input_shape[1], int) and input_shape[1] != self.history_steps:
                    raise ValueError("ONNX history_steps 与 metadata 不一致")
                if (
                    isinstance(input_shape[2], int)
                    and input_shape[2] != len(self.feature_columns)
                ):
                    raise ValueError("ONNX feature_columns 与 metadata 不一致")
                if isinstance(output_shape[1], int) and output_shape[1] != expected_output_width:
                    raise ValueError("ONNX forecast_steps 与 metadata 不一致")
            self.backend = "onnxruntime-cpu"
            warmup_steps = self.history_steps or 5
            warmup = np.ones(
                (1, warmup_steps, len(self.feature_columns)), dtype=np.float32
            )
            self.session.run([self.output_name], {self.input_name: warmup})
        except Exception as exc:  # pragma: no cover - depends on local runtime/model
            self.session = None
            self.backend = "numpy-fallback"
            self.load_error = f"ONNX 模型加载失败，使用 NumPy 兼容推理：{exc}"

    def close(self) -> None:
        self.session = None

    def calendar_feature_values(self, value: date | datetime) -> dict[str, float]:
        """Return calendar features according to this model's metadata contract."""

        local_date = value.date() if isinstance(value, datetime) else value
        if self.holiday_policy == "china_statutory_with_makeup":
            period = next(
                (
                    item
                    for item in self.inference_calendar_periods
                    if item["valid_from"] <= local_date <= item["valid_to"]
                ),
                None,
            )
            if (
                period is None
                and self.holiday_calendar_valid_from is not None
                and self.holiday_calendar_valid_to is not None
                and self.holiday_calendar_valid_from <= local_date <= self.holiday_calendar_valid_to
            ):
                period = {
                    "holiday_dates": self.holiday_dates,
                    "makeup_workdays": self.makeup_workdays,
                }
            if period is None:
                raise ValueError(
                    "模型的中国节假日日历不覆盖日期 "
                    f"{local_date.isoformat()}"
                )
            key = local_date.isoformat()
            return {
                "is_holiday": float(key in period["holiday_dates"]),
                "is_makeup_workday": float(key in period["makeup_workdays"]),
            }
        return {
            "is_holiday": float(local_date.weekday() >= 5),
            "is_makeup_workday": 0.0,
        }

    def info(self) -> dict[str, Any]:
        input_shape = None
        output_shape = None
        if self.session is not None:
            input_shape = self.session.get_inputs()[0].shape
            output_shape = self.session.get_outputs()[0].shape
        return {
            "model": self.model_name,
            "model_type": self.model_type,
            "profile": self.model_profile,
            "deployment_stage": self.deployment_stage,
            "intended_use": self.intended_use,
            "model_path": str(self.model_path),
            "backend": self.backend,
            "providers": self.session.get_providers() if self.session is not None else [],
            "input_name": self.input_name,
            "output_name": self.output_name,
            "input_shape": input_shape,
            "output_shape": output_shape,
            "history_steps": self.history_steps,
            "forecast_steps": self.max_future_steps,
            "bin_seconds": self.bin_seconds,
            "target_column": self.target_column,
            "feature_columns": self.feature_columns,
            "output_includes_current": self.output_includes_current,
            "timezone": self.timezone,
            "holiday_policy": self.holiday_policy,
            "holiday_calendar_source": self.holiday_calendar_source,
            "holiday_calendar_years": self.holiday_calendar_years,
            "holiday_calendar_valid_from": (
                self.holiday_calendar_valid_from.isoformat()
                if self.holiday_calendar_valid_from
                else None
            ),
            "holiday_calendar_valid_to": (
                self.holiday_calendar_valid_to.isoformat()
                if self.holiday_calendar_valid_to
                else None
            ),
            "holiday_dates": sorted(self.holiday_dates),
            "makeup_workdays": sorted(self.makeup_workdays),
            "inference_calendar_years": self.inference_calendar_years,
            "inference_calendar_periods": [
                _calendar_period_info(period) for period in self.inference_calendar_periods
            ],
            "unknown_year_policy": self.unknown_year_policy,
            "missing_bucket_policy": self.missing_bucket_policy,
            "load_error": self.load_error,
        }

    def predict(
        self,
        features: list[Any],
        future_steps: int = 15,
        moving_average_window: int = 5,
        time_step_seconds: int | None = None,
        target_column: str | None = None,
    ) -> PredictionResult:
        started = time.perf_counter()
        batch = _normalise_batch(features, len(self.feature_columns))
        if future_steps > self.max_future_steps:
            raise ValueError(f"当前模型最多支持预测 {self.max_future_steps} 步")
        if self.model_type == "lstm":
            if self.history_steps is not None and batch.shape[1] != self.history_steps:
                raise ValueError(
                    f"当前 LSTM 需要 {self.history_steps} 个历史点，收到 {batch.shape[1]} 个"
                )
            if time_step_seconds is not None and time_step_seconds != self.bin_seconds:
                raise ValueError(
                    f"当前 LSTM 使用 {self.bin_seconds} 秒粒度，收到 {time_step_seconds} 秒"
                )
            if target_column is not None and target_column != self.target_column:
                raise ValueError(
                    f"当前 LSTM 目标列为 {self.target_column}，收到 {target_column}"
                )
        target_history = batch[:, :, self.target_feature_index:self.target_feature_index + 1]
        smoothed = np.stack(
            [_moving_average(sequence[:, 0], moving_average_window) for sequence in target_history],
            axis=0,
        )[:, :, None]
        if self.session is not None and self.model_type == "lstm":
            model_input = (batch - self.feature_means.reshape(1, 1, -1)) / self.feature_stds.reshape(
                1, 1, -1
            )
            forecast = self.session.run(
                [self.output_name], {self.input_name: model_input.astype(np.float32, copy=False)}
            )[0]
            forecast = forecast * self.scaler_std + self.scaler_mean
            forecast = np.maximum(forecast, 0.0)
        elif self.session is not None:
            forecast = self.session.run(
                [self.output_name], {self.input_name: smoothed.astype(np.float32, copy=False)}
            )[0]
        else:
            forecast = _native_forecast(smoothed)
        forecast = np.asarray(forecast, dtype=np.float32)
        elapsed = (time.perf_counter() - started) * 1000
        if self.session is not None and self.model_type == "lstm" and not self.output_includes_current:
            future = forecast[:, :future_steps]
            current = target_history[:, -1, 0]
        else:
            sliced = forecast[:, : future_steps + 1]
            current = sliced[:, 0]
            future = sliced[:, 1:]
        all_forecasts = [[round(float(value), 3) for value in row] for row in future]
        all_flows = [round(float(value), 3) for value in current]
        active_model_name = self.model_name if self.session is not None else MODEL_NAME
        return PredictionResult(
            current_flow=all_flows[0],
            forecast=all_forecasts[0],
            current_flows=all_flows,
            forecasts=all_forecasts,
            latency_ms=round(elapsed, 3),
            backend=self.backend,
            model_name=active_model_name,
            target_column=self.target_column,
        )
