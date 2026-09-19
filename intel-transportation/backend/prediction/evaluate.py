"""Evaluation helpers for traffic forecasting models and simple baselines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


BASELINE_METHODS = ("last_value", "moving_average", "linear_trend")


def _as_float_array(values: Any, name: str) -> np.ndarray:
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a numeric array") from exc
    if array.size == 0:
        raise ValueError(f"{name} cannot be empty")
    return array


def _target_matrix(values: Any, name: str, sample_count: int | None = None) -> np.ndarray:
    array = _as_float_array(values, name)
    if array.ndim == 0:
        return array.reshape(1, 1)
    if array.ndim == 1:
        if sample_count == 1 and array.size != 1:
            return array.reshape(1, -1)
        return array.reshape(-1, 1)
    if array.ndim == 2:
        return array
    if array.ndim == 3 and array.shape[-1] == 1:
        return array[:, :, 0]
    raise ValueError(f"{name} must have shape [samples], [samples, horizon], or [samples, horizon, 1]")


def _window_matrix(windows: Any) -> np.ndarray:
    array = _as_float_array(windows, "windows")
    if array.ndim == 1:
        array = array.reshape(1, -1)
    elif array.ndim == 3 and array.shape[-1] == 1:
        array = array[:, :, 0]
    elif array.ndim != 2:
        raise ValueError("windows must have shape [samples, history] or [samples, history, 1]")
    if array.shape[1] == 0:
        raise ValueError("windows must contain at least one history point")
    return array


def compute_regression_metrics(y_true: Any, y_pred: Any) -> dict[str, float | int | None]:
    """Calculate MAE, RMSE, WAPE, and R-squared after pairwise finite filtering.

    MAE and RMSE remain in the input target's original scale. WAPE is returned
    as a percentage. Undefined WAPE and R-squared values are represented by
    ``None`` so the result can be serialized as strict JSON.
    """

    truth = _as_float_array(y_true, "y_true")
    prediction = _as_float_array(y_pred, "y_pred")
    if truth.shape != prediction.shape:
        raise ValueError(f"y_true and y_pred must have the same shape: {truth.shape} != {prediction.shape}")

    truth = truth.reshape(-1)
    prediction = prediction.reshape(-1)
    valid_mask = np.isfinite(truth) & np.isfinite(prediction)
    valid_truth = truth[valid_mask]
    valid_prediction = prediction[valid_mask]
    valid_count = int(valid_truth.size)
    excluded_count = int(truth.size - valid_count)

    if valid_count == 0:
        return {
            "mae": None,
            "rmse": None,
            "wape": None,
            "r2": None,
            "valid_count": 0,
            "excluded_count": excluded_count,
        }

    residual = valid_truth - valid_prediction
    mae = float(np.mean(np.abs(residual)))
    rmse = float(np.sqrt(np.mean(np.square(residual))))
    denominator = float(np.sum(np.abs(valid_truth)))
    wape = float(np.sum(np.abs(residual)) / denominator * 100.0) if denominator > 0.0 else None

    if valid_count < 2:
        r2 = None
    else:
        total_sum_squares = float(np.sum(np.square(valid_truth - np.mean(valid_truth))))
        residual_sum_squares = float(np.sum(np.square(residual)))
        # 与 sklearn(force_finite=True) 一致：常数目标完美预测为1，否则为0。
        if total_sum_squares <= np.finfo(np.float64).eps:
            r2 = 1.0 if residual_sum_squares <= np.finfo(np.float64).eps else 0.0
        else:
            r2 = float(1.0 - residual_sum_squares / total_sum_squares)

    return {
        "mae": mae,
        "rmse": rmse,
        "wape": wape,
        "r2": r2,
        "valid_count": valid_count,
        "excluded_count": excluded_count,
    }


def baseline_forecast(
    windows: Any,
    forecast_steps: int = 1,
    method: str = "last_value",
    moving_average_window: int = 5,
) -> np.ndarray:
    """Forecast each input window using a deterministic baseline.

    Non-finite history values are ignored. A row without any finite history
    produces ``NaN`` forecasts, which the metric layer subsequently excludes.
    """

    history = _window_matrix(windows)
    if forecast_steps < 1:
        raise ValueError("forecast_steps must be at least 1")
    if method not in BASELINE_METHODS:
        raise ValueError(f"unknown baseline method {method!r}; expected one of {BASELINE_METHODS}")
    if moving_average_window < 1:
        raise ValueError("moving_average_window must be at least 1")

    forecasts = np.full((history.shape[0], forecast_steps), np.nan, dtype=np.float64)
    future_offsets = np.arange(history.shape[1], history.shape[1] + forecast_steps, dtype=np.float64)

    for row_index, row in enumerate(history):
        finite_positions = np.flatnonzero(np.isfinite(row))
        if finite_positions.size == 0:
            continue
        finite_values = row[finite_positions]

        if method == "last_value":
            forecasts[row_index, :] = finite_values[-1]
        elif method == "moving_average":
            forecasts[row_index, :] = float(np.mean(finite_values[-moving_average_window:]))
        elif finite_values.size == 1:
            forecasts[row_index, :] = finite_values[-1]
        else:
            x = finite_positions.astype(np.float64)
            x_centered = x - np.mean(x)
            denominator = float(np.dot(x_centered, x_centered))
            slope = float(np.dot(x_centered, finite_values - np.mean(finite_values)) / denominator)
            intercept = float(np.mean(finite_values) - slope * np.mean(x))
            forecasts[row_index, :] = intercept + slope * future_offsets

    return forecasts


def _expand_metadata(values: Sequence[Any] | None, sample_count: int, name: str) -> list[Any]:
    if values is None:
        return list(range(sample_count)) if name == "sample_id" else [None] * sample_count
    expanded = list(values)
    if len(expanded) != sample_count:
        raise ValueError(f"{name} must contain one value per sample")
    return expanded


def _last_finite_values(windows: np.ndarray) -> list[float | None]:
    result: list[float | None] = []
    for row in windows:
        finite = row[np.isfinite(row)]
        result.append(float(finite[-1]) if finite.size else None)
    return result


def _write_residual_plot(residuals: np.ndarray, output_path: Path) -> None:
    figure, axis = plt.subplots(figsize=(7.2, 4.2), constrained_layout=True)
    if residuals.size:
        bins = min(30, max(1, int(np.sqrt(residuals.size))))
        axis.hist(residuals, bins=bins, color="#2878b5", edgecolor="white", alpha=0.9)
        axis.axvline(0.0, color="#d1495b", linestyle="--", linewidth=1.4, label="Zero residual")
        axis.legend(frameon=False)
    else:
        axis.text(0.5, 0.5, "No finite residuals", ha="center", va="center", transform=axis.transAxes)
    axis.set_title("Residual distribution")
    axis.set_xlabel("Residual (y_true - y_pred)")
    axis.set_ylabel("Frequency")
    axis.grid(axis="y", alpha=0.2)
    figure.savefig(output_path, dpi=160)
    plt.close(figure)


def evaluate_predictions(
    y_true: Any,
    y_pred: Any,
    output_dir: str | Path,
    *,
    windows: Any | None = None,
    model_name: str = "model",
    sample_ids: Sequence[Any] | None = None,
    series_ids: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Evaluate supplied predictions and write CSV, JSON, and PNG artifacts.

    This is the primary integration point for ``train_lstm.py``. Passing its
    test windows is optional, but adds ``history_last_value`` to the prediction
    table and validates that sample dimensions remain aligned.
    """

    history = _window_matrix(windows) if windows is not None else None
    sample_count = int(history.shape[0]) if history is not None else None
    truth = _target_matrix(y_true, "y_true", sample_count)
    prediction = _target_matrix(y_pred, "y_pred", sample_count)
    if truth.shape != prediction.shape:
        raise ValueError(f"y_true and y_pred must have the same shape: {truth.shape} != {prediction.shape}")
    if history is not None and history.shape[0] != truth.shape[0]:
        raise ValueError("windows, y_true, and y_pred must contain the same number of samples")

    metrics = compute_regression_metrics(truth, prediction)
    metrics.update(
        {
            "model_name": str(model_name),
            "sample_count": int(truth.shape[0]),
            "forecast_steps": int(truth.shape[1]),
        }
    )

    sample_values = _expand_metadata(sample_ids, truth.shape[0], "sample_id")
    series_values = _expand_metadata(series_ids, truth.shape[0], "series_id")
    history_last = _last_finite_values(history) if history is not None else [None] * truth.shape[0]
    records: list[dict[str, Any]] = []
    residual_records: list[dict[str, Any]] = []

    for sample_index in range(truth.shape[0]):
        for horizon_index in range(truth.shape[1]):
            actual = float(truth[sample_index, horizon_index])
            predicted = float(prediction[sample_index, horizon_index])
            is_valid = bool(np.isfinite(actual) and np.isfinite(predicted))
            residual = actual - predicted if is_valid else np.nan
            common = {
                "sample_id": sample_values[sample_index],
                "series_id": series_values[sample_index],
                "horizon_step": horizon_index + 1,
            }
            records.append(
                {
                    **common,
                    "y_true": actual,
                    "y_pred": predicted,
                    "residual": residual,
                    "is_valid": is_valid,
                    "history_last_value": history_last[sample_index],
                    "model_name": str(model_name),
                }
            )
            if is_valid:
                residual_records.append(
                    {
                        **common,
                        "residual": residual,
                        "absolute_error": abs(residual),
                        "squared_error": residual * residual,
                    }
                )

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    predictions_path = destination / "predictions.csv"
    residuals_path = destination / "residuals.csv"
    metrics_path = destination / "metrics.json"
    plot_path = destination / "residual_distribution.png"

    pd.DataFrame.from_records(records).to_csv(predictions_path, index=False)
    pd.DataFrame.from_records(
        residual_records,
        columns=["sample_id", "series_id", "horizon_step", "residual", "absolute_error", "squared_error"],
    ).to_csv(residuals_path, index=False)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    _write_residual_plot(np.asarray([row["residual"] for row in residual_records]), plot_path)

    return {
        "metrics": metrics,
        "paths": {
            "predictions": predictions_path,
            "residuals": residuals_path,
            "metrics": metrics_path,
            "residual_distribution": plot_path,
        },
    }


def evaluate_baseline(
    windows: Any,
    y_true: Any,
    output_dir: str | Path,
    *,
    method: str = "last_value",
    moving_average_window: int = 5,
    sample_ids: Sequence[Any] | None = None,
    series_ids: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Generate one baseline forecast and evaluate it with the standard outputs."""

    history = _window_matrix(windows)
    truth = _target_matrix(y_true, "y_true", history.shape[0])
    if truth.shape[0] != history.shape[0]:
        raise ValueError("windows and y_true must contain the same number of samples")
    prediction = baseline_forecast(
        history,
        forecast_steps=truth.shape[1],
        method=method,
        moving_average_window=moving_average_window,
    )
    return evaluate_predictions(
        truth,
        prediction,
        output_dir,
        windows=history,
        model_name=method,
        sample_ids=sample_ids,
        series_ids=series_ids,
    )


__all__ = [
    "BASELINE_METHODS",
    "baseline_forecast",
    "compute_regression_metrics",
    "evaluate_baseline",
    "evaluate_predictions",
]
