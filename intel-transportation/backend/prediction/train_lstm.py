"""Train, validate, evaluate, and export traffic-flow LSTM models."""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import random
from typing import Any

import numpy as np
import pandas as pd

try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
except ImportError:  # pragma: no cover - explicit CLI validation
    torch = None

from .data import (
    CHINA_CHECKPOINT_FEATURE_COLUMNS,
    TEACHER_FEATURE_COLUMNS,
    build_windows,
    load_flow_series,
    load_flow_series_set,
    load_teacher_compat_series,
    prepare_teacher_compat_partitions,
    prepare_window_partitions,
)
from .evaluate import (
    BASELINE_METHODS,
    compute_regression_metrics,
    evaluate_baseline,
    evaluate_predictions,
)
from .holiday_calendar import china_prediction_calendar_metadata, kdd2017_calendar_metadata
from .lstm import LSTMForecastModel


PROFILES = ("production_multistep", "teacher_compat", "china_kdd2017")


@dataclass(frozen=True)
class TrainingConfig:
    profile: str = "production_multistep"
    history_steps: int = 20
    forecast_steps: int = 15
    bin_seconds: int = 60
    hidden_size: int = 32
    num_layers: int = 2
    hidden_sizes: tuple[int, ...] | None = None
    dropout: float = 0.1
    fc_hidden_size: int | None = None
    epochs: int = 150
    batch_size: int = 32
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    validation_ratio: float = 0.2
    test_ratio: float = 0.15
    purge_steps: int = 0
    target_column: str = "vehicle_count"
    feature_columns: tuple[str, ...] = ("vehicle_count",)
    scaler_type: str = "standard"
    min_bucket_coverage_ratio: float = 0.95
    minimum_training_windows: int = 20
    scheduler_factor: float = 0.5
    scheduler_patience: int = 3
    early_stopping_patience: int = 10
    early_stopping_min_delta: float = 0.001
    gradient_clip_norm: float = 1.0
    seed: int = 20260911
    timezone: str = "UTC"
    holiday_policy: str = "none"
    holiday_calendar_source: str | None = None
    holiday_calendar_years: tuple[int, ...] = ()
    holiday_calendar_valid_from: str | None = None
    holiday_calendar_valid_to: str | None = None
    holiday_dates: tuple[str, ...] = ()
    makeup_workdays: tuple[str, ...] = ()
    missing_bucket_policy: str = "split_on_gap"


def teacher_compat_config(**overrides: Any) -> TrainingConfig:
    """Return the teacher's documented four-feature, one-step configuration."""

    config = TrainingConfig(
        profile="teacher_compat",
        history_steps=6,
        forecast_steps=1,
        bin_seconds=3600,
        hidden_sizes=(64, 32),
        dropout=0.2,
        fc_hidden_size=16,
        epochs=100,
        batch_size=64,
        validation_ratio=0.15,
        test_ratio=0.15,
        feature_columns=TEACHER_FEATURE_COLUMNS,
        scaler_type="minmax",
        timezone="America/Chicago",
        holiday_policy="source_holiday_or_weekend",
    )
    return replace(config, **overrides)


def china_kdd2017_config(**overrides: Any) -> TrainingConfig:
    """Return the China tollgate profile with statutory holiday features."""

    calendar = kdd2017_calendar_metadata()
    config = TrainingConfig(
        profile="china_kdd2017",
        history_steps=6,
        forecast_steps=1,
        bin_seconds=1200,
        hidden_sizes=(64, 32),
        dropout=0.2,
        fc_hidden_size=16,
        epochs=100,
        batch_size=64,
        validation_ratio=0.15,
        test_ratio=0.15,
        feature_columns=CHINA_CHECKPOINT_FEATURE_COLUMNS,
        scaler_type="minmax",
        timezone="Asia/Shanghai",
        holiday_policy=str(calendar["holiday_policy"]),
        holiday_calendar_source=str(calendar["holiday_calendar_source"]),
        holiday_calendar_years=tuple(calendar["holiday_calendar_years"]),
        holiday_calendar_valid_from=str(calendar["holiday_calendar_valid_from"]),
        holiday_calendar_valid_to=str(calendar["holiday_calendar_valid_to"]),
        holiday_dates=tuple(calendar["holiday_dates"]),
        makeup_workdays=tuple(calendar["makeup_workdays"]),
        missing_bucket_policy="split_on_gap",
    )
    return replace(config, **overrides)


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _validate_config(config: TrainingConfig) -> None:
    if config.profile not in PROFILES:
        raise ValueError(f"profile 必须为 {PROFILES} 之一")
    if config.epochs < 1 or config.batch_size < 1 or config.learning_rate <= 0:
        raise ValueError("epochs、batch_size 和 learning_rate 必须为正数")
    if config.history_steps < 1 or config.forecast_steps < 1 or config.bin_seconds < 1:
        raise ValueError("history_steps、forecast_steps 和 bin_seconds 必须为正整数")
    if config.weight_decay < 0 or config.gradient_clip_norm <= 0:
        raise ValueError("weight_decay 不能为负数，gradient_clip_norm 必须为正数")
    if not 0 < config.scheduler_factor < 1 or config.scheduler_patience < 0:
        raise ValueError("scheduler_factor 必须位于 (0, 1)，scheduler_patience 不能为负数")
    if config.early_stopping_patience < 1 or config.early_stopping_min_delta < 0:
        raise ValueError("early stopping patience 必须为正数且 min_delta 不能为负数")
    if config.scaler_type not in {"standard", "minmax"}:
        raise ValueError("scaler_type 必须为 standard 或 minmax")
    if config.profile in {"teacher_compat", "china_kdd2017"}:
        expected_features = (
            TEACHER_FEATURE_COLUMNS
            if config.profile == "teacher_compat"
            else CHINA_CHECKPOINT_FEATURE_COLUMNS
        )
        if tuple(config.feature_columns) != expected_features:
            raise ValueError(f"{config.profile} 特征必须为 {expected_features}")
        if config.target_column != "vehicle_count" or config.forecast_steps != 1:
            raise ValueError(f"{config.profile} 仅支持 vehicle_count 单步预测")
    if not config.timezone.strip():
        raise ValueError("timezone 不能为空")
    if config.holiday_policy == "china_statutory_with_makeup":
        if (
            not config.holiday_calendar_source
            or not config.holiday_calendar_valid_from
            or not config.holiday_calendar_valid_to
            or not config.holiday_dates
        ):
            raise ValueError("中国节假日策略必须提供来源、有效期和节假日日期")


def _fit_scaler(values: np.ndarray, scaler_type: str) -> tuple[np.ndarray, np.ndarray]:
    array = np.asarray(values, dtype=np.float32)
    if array.ndim == 1:
        array = array[:, None]
    if scaler_type == "minmax":
        offset = array.min(axis=0)
        scale = array.max(axis=0) - offset
    else:
        offset = array.mean(axis=0)
        scale = array.std(axis=0)
    return offset.astype(np.float32), np.maximum(scale, 1e-6).astype(np.float32)


def _scale_features(values: np.ndarray, offset: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return ((values - offset.reshape(1, 1, -1)) / scale.reshape(1, 1, -1)).astype(
        np.float32,
        copy=False,
    )


def _future_output(model: LSTMForecastModel, output: torch.Tensor) -> torch.Tensor:
    return output[:, 1:] if model.include_current else output


def _predict_normalized(
    model: LSTMForecastModel,
    features: np.ndarray,
    device: torch.device,
) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        prediction = _future_output(
            model,
            model(torch.from_numpy(features).to(device)),
        )
    return prediction.detach().cpu().numpy()


def _write_training_logs(records: list[dict[str, Any]], artifact_dir: Path) -> dict[str, str]:
    frame = pd.DataFrame.from_records(records)
    csv_path = artifact_dir / "training_log.csv"
    jsonl_path = artifact_dir / "training_log.jsonl"
    frame.to_csv(csv_path, index=False)
    with jsonl_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    return {"csv": str(csv_path), "jsonl": str(jsonl_path)}


def _train_arrays(
    *,
    train_features: np.ndarray,
    train_targets: np.ndarray,
    validation_features: np.ndarray,
    validation_targets: np.ndarray,
    model: LSTMForecastModel,
    config: TrainingConfig,
    target_offset: float,
    target_scale: float,
) -> tuple[LSTMForecastModel, dict[str, Any], list[dict[str, Any]]]:
    train_dataset = TensorDataset(
        torch.from_numpy(train_features),
        torch.from_numpy(train_targets),
    )
    generator = torch.Generator().manual_seed(config.seed)
    loader = DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
    )
    if len(loader) == 0:
        raise ValueError("训练数据为空，无法创建 DataLoader")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        mode="min",
        factor=config.scheduler_factor,
        patience=config.scheduler_patience,
    )
    loss_fn = nn.MSELoss()
    validation_feature_tensor = torch.from_numpy(validation_features).to(device)
    validation_target_tensor = torch.from_numpy(validation_targets).to(device)

    best_loss = float("inf")
    best_state = None
    best_optimizer_state = None
    best_scheduler_state = None
    best_epoch = 0
    early_stopping_reference = float("inf")
    stale_epochs = 0
    history: list[dict[str, Any]] = []

    for epoch in range(1, config.epochs + 1):
        model.train()
        running_loss = 0.0
        sample_count = 0
        for batch_features, batch_targets in loader:
            batch_features = batch_features.to(device)
            batch_targets = batch_targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            prediction = _future_output(model, model(batch_features))
            loss = loss_fn(prediction, batch_targets)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=config.gradient_clip_norm)
            optimizer.step()
            running_loss += float(loss.item()) * len(batch_features)
            sample_count += len(batch_features)

        model.eval()
        with torch.no_grad():
            validation_prediction = _future_output(
                model,
                model(validation_feature_tensor),
            )
            validation_loss = float(
                loss_fn(validation_prediction, validation_target_tensor).item()
            )
        validation_prediction_raw = (
            validation_prediction.detach().cpu().numpy() * target_scale + target_offset
        )
        validation_target_raw = validation_targets * target_scale + target_offset
        validation_metrics = compute_regression_metrics(
            validation_target_raw,
            validation_prediction_raw,
        )
        current_lr = float(optimizer.param_groups[0]["lr"])
        history.append(
            {
                "epoch": epoch,
                "train_loss_normalized": running_loss / max(sample_count, 1),
                "validation_loss_normalized": validation_loss,
                "validation_mae": validation_metrics["mae"],
                "validation_rmse": validation_metrics["rmse"],
                "validation_wape": validation_metrics["wape"],
                "validation_r2": validation_metrics["r2"],
                "learning_rate": current_lr,
            }
        )

        if best_state is None or validation_loss < best_loss:
            best_loss = validation_loss
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }
            best_optimizer_state = copy.deepcopy(optimizer.state_dict())
            best_scheduler_state = copy.deepcopy(scheduler.state_dict())
            best_epoch = epoch

        if validation_loss < early_stopping_reference - config.early_stopping_min_delta:
            early_stopping_reference = validation_loss
            stale_epochs = 0
        else:
            stale_epochs += 1

        scheduler.step(validation_loss)
        if stale_epochs >= config.early_stopping_patience:
            break

    if best_state is None:
        raise RuntimeError("训练未生成有效的最佳模型状态")
    model.load_state_dict(best_state)
    model = model.cpu().eval()
    state = {
        "state_dict": best_state,
        "optimizer_state": best_optimizer_state,
        "scheduler_state": best_scheduler_state,
        "best_epoch": best_epoch,
        "validation_loss": best_loss,
        "stopped_epoch": len(history),
        "early_stopped": len(history) < config.epochs,
        "device": str(device),
    }
    return model, state, history


def _export_onnx(
    model: LSTMForecastModel,
    output: Path,
    history_steps: int,
    feature_count: int,
    comparison_features: np.ndarray,
) -> float | None:
    dummy = torch.zeros((1, history_steps, feature_count), dtype=torch.float32)
    torch.onnx.export(
        model,
        dummy,
        str(output),
        input_names=["features"],
        output_names=["forecast"],
        dynamic_axes={"features": {0: "batch"}, "forecast": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    try:
        import onnx
        import onnxruntime as ort

        onnx.checker.check_model(onnx.load(output))
        batch = comparison_features[: min(2, len(comparison_features))].astype(np.float32)
        with torch.no_grad():
            expected = model(torch.from_numpy(batch)).numpy()
        session = ort.InferenceSession(str(output), providers=["CPUExecutionProvider"])
        actual = session.run(None, {session.get_inputs()[0].name: batch})[0]
        return float(np.max(np.abs(expected - actual)))
    except ImportError:
        return None


def _serialize_config(config: TrainingConfig) -> dict[str, Any]:
    return asdict(config)


def _source_metadata(paths: list[str | Path]) -> list[dict[str, object]]:
    result = []
    for value in paths:
        path = Path(value).expanduser().resolve()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result.append(
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return result


def _train_teacher_model(
    paths: list[str | Path],
    output: Path,
    config: TrainingConfig,
) -> dict[str, object]:
    series_set = []
    for path in paths:
        series_set.extend(
            load_teacher_compat_series(
                path,
                feature_columns=tuple(config.feature_columns),
                target_column=config.target_column,
                bin_seconds=config.bin_seconds,
                holiday_policy=config.holiday_policy,
            )
        )
    partitions = prepare_teacher_compat_partitions(
        series_set,
        history_steps=config.history_steps,
        validation_ratio=config.validation_ratio,
        test_ratio=config.test_ratio,
        purge_steps=config.purge_steps,
    )
    if len(partitions.train.features) < config.minimum_training_windows:
        raise ValueError(
            f"训练窗口只有 {len(partitions.train.features)} 个，至少需要 "
            f"{config.minimum_training_windows} 个"
        )

    feature_offset, feature_scale = _fit_scaler(partitions.scaler_values, config.scaler_type)
    target_index = partitions.feature_columns.index(partitions.target_column)
    target_offset = float(feature_offset[target_index])
    target_scale = float(feature_scale[target_index])
    train_features = _scale_features(partitions.train.features, feature_offset, feature_scale)
    validation_features = _scale_features(
        partitions.validation.features,
        feature_offset,
        feature_scale,
    )
    test_features = _scale_features(partitions.test.features, feature_offset, feature_scale)
    train_targets = ((partitions.train.targets - target_offset) / target_scale).astype(np.float32)
    validation_targets = (
        (partitions.validation.targets - target_offset) / target_scale
    ).astype(np.float32)

    model = LSTMForecastModel.teacher_compat(
        input_size=len(partitions.feature_columns),
        hidden_sizes=config.hidden_sizes or (64, 32),
        dropout=config.dropout,
        fc_hidden_size=config.fc_hidden_size or 16,
        forecast_steps=config.forecast_steps,
    )
    model, state, history = _train_arrays(
        train_features=train_features,
        train_targets=train_targets,
        validation_features=validation_features,
        validation_targets=validation_targets,
        model=model,
        config=config,
        target_offset=target_offset,
        target_scale=target_scale,
    )

    artifact_dir = output.parent / f"{output.stem}_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    training_log_paths = _write_training_logs(history, artifact_dir)
    skipped_series_path = artifact_dir / "skipped_series.json"
    skipped_series_path.write_text(
        json.dumps(
            list(partitions.skipped_series),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    test_prediction_normalized = _predict_normalized(model, test_features, torch.device("cpu"))
    test_prediction = test_prediction_normalized * target_scale + target_offset
    model_evaluation = evaluate_predictions(
        partitions.test.targets,
        test_prediction,
        artifact_dir / "test_model",
        windows=partitions.test.features[:, :, target_index],
        model_name=output.stem,
        sample_ids=partitions.test.target_times.tolist(),
        series_ids=partitions.test.series_keys,
    )
    baseline_metrics: dict[str, Any] = {}
    for method in BASELINE_METHODS:
        baseline_result = evaluate_baseline(
            partitions.test.features[:, :, target_index],
            partitions.test.targets,
            artifact_dir / f"baseline_{method}",
            method=method,
            sample_ids=partitions.test.target_times.tolist(),
            series_ids=partitions.test.series_keys,
        )
        baseline_metrics[method] = baseline_result["metrics"]
    test_series_keys = np.asarray(partitions.test.series_keys, dtype=object)
    per_series_metrics = {
        str(series_key): compute_regression_metrics(
            partitions.test.targets[test_series_keys == series_key],
            test_prediction[test_series_keys == series_key],
        )
        for series_key in dict.fromkeys(partitions.test.series_keys)
    }
    metrics_summary = {
        "model": model_evaluation["metrics"],
        "baselines": baseline_metrics,
        "per_series": per_series_metrics,
    }
    metrics_summary_path = artifact_dir / "metrics_summary.json"
    metrics_summary_path.write_text(
        json.dumps(metrics_summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    per_series_metrics_path = artifact_dir / "per_series_metrics.json"
    per_series_metrics_path.write_text(
        json.dumps(per_series_metrics, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    scaler = {
        "type": config.scaler_type,
        "feature_columns": list(partitions.feature_columns),
        "feature_offset": feature_offset.tolist(),
        "feature_scale": feature_scale.tolist(),
        "target_column": partitions.target_column,
        "target_offset": target_offset,
        "target_scale": target_scale,
        "fit_partition": "train",
    }
    scaler_path = output.with_name(f"{output.stem}_scaler.json")
    scaler_path.write_text(
        json.dumps(scaler, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    onnx_error = _export_onnx(
        model,
        output,
        config.history_steps,
        len(partitions.feature_columns),
        test_features,
    )

    checkpoint = {
        **state,
        "config": _serialize_config(config),
        "scaler": scaler,
        "metrics": metrics_summary,
    }
    torch.save(checkpoint, output.with_suffix(".pt"))
    metadata = {
        "model": output.stem,
        "model_type": "lstm",
        "profile": config.profile,
        "history_steps": config.history_steps,
        "forecast_steps": config.forecast_steps,
        "bin_seconds": config.bin_seconds,
        "target_column": partitions.target_column,
        "feature_columns": list(partitions.feature_columns),
        "scaler_type": config.scaler_type,
        "feature_offsets": feature_offset.tolist(),
        "feature_scales": feature_scale.tolist(),
        "target_offset": target_offset,
        "target_scale": target_scale,
        "output_includes_current": False,
        "split_strategy": "global_chronological",
        "validation_ratio": config.validation_ratio,
        "test_ratio": config.test_ratio,
        "purge_steps": config.purge_steps,
        "missing_bucket_policy": config.missing_bucket_policy,
        "timezone": config.timezone,
        "holiday_policy": config.holiday_policy,
        "holiday_calendar_source": config.holiday_calendar_source,
        "holiday_calendar_years": list(config.holiday_calendar_years),
        "holiday_calendar_valid_from": config.holiday_calendar_valid_from,
        "holiday_calendar_valid_to": config.holiday_calendar_valid_to,
        "holiday_dates": list(config.holiday_dates),
        "makeup_workdays": list(config.makeup_workdays),
        "training_data": _source_metadata(paths),
        "best_epoch": state["best_epoch"],
        "stopped_epoch": state["stopped_epoch"],
        "early_stopped": state["early_stopped"],
        "validation_mse_normalized": state["validation_loss"],
        "training_windows": len(partitions.train.features),
        "validation_windows": len(partitions.validation.features),
        "test_windows": len(partitions.test.features),
        "skipped_series_count": len(partitions.skipped_series),
        "skipped_series_preview": list(partitions.skipped_series[:20]),
        "split_time_boundaries": {
            "train_end": pd.to_datetime(
                int(partitions.train.target_times.max()), unit="s"
            ).isoformat(),
            "validation_start": pd.to_datetime(
                int(partitions.validation.feature_times.min()), unit="s"
            ).isoformat(),
            "validation_end": pd.to_datetime(
                int(partitions.validation.target_times.max()), unit="s"
            ).isoformat(),
            "test_start": pd.to_datetime(
                int(partitions.test.feature_times.min()), unit="s"
            ).isoformat(),
        },
        "test_metrics": model_evaluation["metrics"],
        "baseline_metrics": baseline_metrics,
        "onnx_max_abs_error": onnx_error,
        "checkpoint": str(output.with_suffix(".pt")),
        "scaler": str(scaler_path),
        "artifacts": {
            "directory": str(artifact_dir),
            "training_logs": training_log_paths,
            "metrics_summary": str(metrics_summary_path),
            "per_series_metrics": str(per_series_metrics_path),
            "skipped_series": str(skipped_series_path),
            "model_evaluation": {
                key: str(value) for key, value in model_evaluation["paths"].items()
            },
        },
        "sources": [str(Path(path).expanduser().resolve()) for path in paths],
        "config": _serialize_config(config),
    }
    if config.profile == "china_kdd2017":
        metadata.update(china_prediction_calendar_metadata())
        metadata["deployment_stage"] = "research_candidate"
        metadata["intended_use"] = "coursework_research_and_noncommercial_demo"
    output.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return metadata


def _train_legacy_model(
    paths: list[str | Path],
    output: Path,
    config: TrainingConfig,
) -> dict[str, object]:
    series_set = []
    for path in paths:
        series_set.extend(
            load_flow_series_set(
                path,
                bin_seconds=config.bin_seconds,
                target_column=config.target_column,
                min_bucket_coverage_ratio=config.min_bucket_coverage_ratio,
            )
        )
    partitions = prepare_window_partitions(
        series_set,
        config.history_steps,
        config.forecast_steps,
        validation_ratio=config.validation_ratio,
        purge_steps=config.purge_steps,
    )
    if len(partitions.train_features) < config.minimum_training_windows:
        raise ValueError(
            f"无泄漏切分后的训练窗口只有 {len(partitions.train_features)} 个，"
            f"至少需要 {config.minimum_training_windows} 个；请提供更长或更多独立场景数据"
        )

    feature_offset, feature_scale = _fit_scaler(partitions.scaler_values, "standard")
    target_offset = float(feature_offset[0])
    target_scale = float(feature_scale[0])
    train_features = _scale_features(partitions.train_features, feature_offset, feature_scale)
    validation_features = _scale_features(
        partitions.validation_features,
        feature_offset,
        feature_scale,
    )
    train_targets = ((partitions.train_targets - target_offset) / target_scale).astype(np.float32)
    validation_targets = (
        (partitions.validation_targets - target_offset) / target_scale
    ).astype(np.float32)
    model = LSTMForecastModel(
        config.hidden_size,
        config.num_layers,
        config.forecast_steps,
        dropout=config.dropout,
    )
    model, state, history = _train_arrays(
        train_features=train_features,
        train_targets=train_targets,
        validation_features=validation_features,
        validation_targets=validation_targets,
        model=model,
        config=config,
        target_offset=target_offset,
        target_scale=target_scale,
    )
    artifact_dir = output.parent / f"{output.stem}_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    training_log_paths = _write_training_logs(history, artifact_dir)
    onnx_error = _export_onnx(
        model,
        output,
        config.history_steps,
        1,
        validation_features,
    )
    scaler = {
        "type": "standard",
        "feature_columns": [config.target_column],
        "feature_offset": feature_offset.tolist(),
        "feature_scale": feature_scale.tolist(),
        "target_column": config.target_column,
        "target_offset": target_offset,
        "target_scale": target_scale,
        "fit_partition": "train",
    }
    torch.save(
        {**state, "config": _serialize_config(config), "scaler": scaler},
        output.with_suffix(".pt"),
    )
    metadata = {
        "model": output.stem,
        "model_type": "lstm",
        "profile": config.profile,
        "mean": target_offset,
        "std": target_scale,
        "feature_offsets": feature_offset.tolist(),
        "feature_scales": feature_scale.tolist(),
        "target_offset": target_offset,
        "target_scale": target_scale,
        "history_steps": config.history_steps,
        "forecast_steps": config.forecast_steps,
        "bin_seconds": config.bin_seconds,
        "target_column": config.target_column,
        "feature_columns": [config.target_column],
        "output_includes_current": True,
        "split_strategy": partitions.split_strategy,
        "purge_steps": config.purge_steps,
        "missing_bucket_policy": "split_on_gap",
        "validation_mse_normalized": state["validation_loss"],
        "best_epoch": state["best_epoch"],
        "stopped_epoch": state["stopped_epoch"],
        "early_stopped": state["early_stopped"],
        "training_windows": len(partitions.train_features),
        "validation_windows": len(partitions.validation_features),
        "training_series": list(partitions.training_series),
        "validation_series": list(partitions.validation_series),
        "skipped_series": list(partitions.skipped_series),
        "onnx_max_abs_error": onnx_error,
        "artifacts": {"directory": str(artifact_dir), "training_logs": training_log_paths},
        "sources": [str(Path(path).expanduser().resolve()) for path in paths],
        "config": _serialize_config(config),
    }
    output.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return metadata


def train_model(
    paths: list[str | Path],
    output_path: str | Path,
    config: TrainingConfig,
) -> dict[str, object]:
    if torch is None:
        raise RuntimeError("训练 LSTM 需要安装 PyTorch")
    _validate_config(config)
    if not paths:
        raise ValueError("至少需要一个训练数据文件")
    _set_seed(config.seed)
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if config.profile in {"teacher_compat", "china_kdd2017"}:
        return _train_teacher_model(paths, output, config)
    return _train_legacy_model(paths, output, config)


def _parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="训练、评估并导出交通流量 PyTorch LSTM")
    parser.add_argument("data", nargs="+", help="轨迹 Excel、原始事件 CSV 或聚合流量 CSV")
    parser.add_argument("--profile", choices=PROFILES, default="production_multistep")
    parser.add_argument("--output", default="backend/prediction/models/traffic_lstm_v1.onnx")
    parser.add_argument("--history-steps", type=int)
    parser.add_argument("--forecast-steps", type=int)
    parser.add_argument("--bin-seconds", type=int)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--weight-decay", type=float)
    parser.add_argument("--validation-ratio", type=float)
    parser.add_argument("--test-ratio", type=float)
    parser.add_argument("--purge-steps", type=int)
    parser.add_argument(
        "--target-column",
        choices=("vehicle_count", "entering_vehicle_count"),
        default="vehicle_count",
        help="聚合或轨迹数据的预测目标",
    )
    parser.add_argument("--minimum-training-windows", type=int)
    parser.add_argument("--early-stopping-patience", type=int)
    parser.add_argument("--early-stopping-min-delta", type=float)
    parser.add_argument("--scheduler-patience", type=int)
    parser.add_argument("--timezone")
    return parser.parse_args(argv)


def _config_from_args(args: argparse.Namespace) -> TrainingConfig:
    if args.profile == "teacher_compat":
        config = teacher_compat_config()
    elif args.profile == "china_kdd2017":
        config = china_kdd2017_config()
    else:
        config = TrainingConfig()
    overrides = {"profile": args.profile, "target_column": args.target_column}
    for field in (
        "history_steps",
        "forecast_steps",
        "bin_seconds",
        "epochs",
        "batch_size",
        "learning_rate",
        "weight_decay",
        "validation_ratio",
        "test_ratio",
        "purge_steps",
        "minimum_training_windows",
        "early_stopping_patience",
        "early_stopping_min_delta",
        "scheduler_patience",
        "timezone",
    ):
        value = getattr(args, field)
        if value is not None:
            overrides[field] = value
    return replace(config, **overrides)


if __name__ == "__main__":
    args = _parse_args()
    settings = _config_from_args(args)
    print(json.dumps(train_model(args.data, args.output, settings), ensure_ascii=False, indent=2))
