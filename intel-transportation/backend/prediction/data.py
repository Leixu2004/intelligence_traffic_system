"""Series-aware data loading for traffic-flow forecasting."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


COUNT_COLUMNS = ("vehicle_count", "entering_vehicle_count")
TIME_COLUMNS = ("timestamp(ms)", "Time", "time")
VEHICLE_ID_COLUMNS = ("track_id", "ID", "vehicle_id")
TEACHER_FEATURE_COLUMNS = (
    "vehicle_count",
    "hour",
    "day_of_week",
    "is_holiday",
)
CHINA_CHECKPOINT_FEATURE_COLUMNS = (
    "vehicle_count",
    "hour",
    "minute",
    "day_of_week",
    "is_holiday",
    "is_makeup_workday",
)
SUPPORTED_CALENDAR_FEATURE_COLUMNS = {
    "vehicle_count",
    "hour",
    "minute",
    "day_of_week",
    "is_holiday",
    "is_makeup_workday",
}


@dataclass(frozen=True)
class FlowSeries:
    """One continuous traffic-flow series from one source and scene."""

    key: str
    series_id: str
    source_path: Path
    bin_seconds: int
    target_column: str
    bucket_starts: np.ndarray
    values: np.ndarray


@dataclass(frozen=True)
class WindowPartitions:
    train_features: np.ndarray
    train_targets: np.ndarray
    validation_features: np.ndarray
    validation_targets: np.ndarray
    scaler_values: np.ndarray
    split_strategy: str
    training_series: tuple[str, ...]
    validation_series: tuple[str, ...]
    skipped_series: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class TeacherCompatSeries:
    """One continuous multivariate series for the teacher-compatible model."""

    key: str
    series_id: str
    source_path: Path
    bin_seconds: int
    feature_columns: tuple[str, ...]
    target_column: str
    bucket_starts: np.ndarray
    features: np.ndarray
    targets: np.ndarray


@dataclass(frozen=True)
class TeacherWindowSet:
    """Supervised windows plus their original scene and time coordinates."""

    features: np.ndarray
    targets: np.ndarray
    feature_times: np.ndarray
    target_times: np.ndarray
    series_keys: tuple[str, ...]


@dataclass(frozen=True)
class TeacherWindowPartitions:
    """Leakage-free train, validation and test partitions."""

    train: TeacherWindowSet
    validation: TeacherWindowSet
    test: TeacherWindowSet
    scaler_values: np.ndarray
    feature_columns: tuple[str, ...]
    target_column: str
    skipped_series: tuple[dict[str, object], ...]


def _validate_steps(history_steps: int, forecast_steps: int) -> None:
    if history_steps < 1 or forecast_steps < 1:
        raise ValueError("history_steps 和 forecast_steps 必须是正整数")


def build_windows(
    series: np.ndarray,
    history_steps: int,
    forecast_steps: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Build direct multi-step windows without reordering the source series."""

    _validate_steps(history_steps, forecast_steps)
    values = np.asarray(series, dtype=np.float32).reshape(-1)
    window_count = len(values) - history_steps - forecast_steps + 1
    if window_count < 1:
        raise ValueError(
            f"时间序列至少需要 {history_steps + forecast_steps} 个点，当前只有 {len(values)} 个"
        )
    features = np.stack(
        [values[index:index + history_steps] for index in range(window_count)]
    )[:, :, None]
    targets = np.stack(
        [
            values[index + history_steps:index + history_steps + forecast_steps]
            for index in range(window_count)
        ]
    )
    return features, targets


def _find_column(columns: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    available = set(columns)
    return next((name for name in candidates if name in available), None)


def _split_contiguous_series(
    *,
    source: Path,
    series_id: str,
    target_column: str,
    bin_seconds: int,
    bucket_starts: np.ndarray,
    values: np.ndarray,
) -> list[FlowSeries]:
    if len(bucket_starts) != len(values):
        raise ValueError(f"{source} 的时间桶和值数量不一致")
    if len(values) < 2:
        raise ValueError(f"{source} 的序列 {series_id} 少于 2 个时间点")

    order = np.argsort(bucket_starts, kind="stable")
    ordered_buckets = np.asarray(bucket_starts, dtype=np.int64)[order]
    ordered_values = np.asarray(values, dtype=np.float32)[order]
    if len(np.unique(ordered_buckets)) != len(ordered_buckets):
        raise ValueError(f"{source} 的序列 {series_id} 存在重复时间桶")

    breaks = np.flatnonzero(np.diff(ordered_buckets) != bin_seconds) + 1
    bucket_segments = np.split(ordered_buckets, breaks)
    value_segments = np.split(ordered_values, breaks)
    result: list[FlowSeries] = []
    for index, (segment_buckets, segment_values) in enumerate(
        zip(bucket_segments, value_segments, strict=True), start=1
    ):
        if len(segment_values) < 2:
            continue
        segment_id = series_id if len(bucket_segments) == 1 else f"{series_id}#segment{index}"
        result.append(
            FlowSeries(
                key=f"{source}:{segment_id}",
                series_id=segment_id,
                source_path=source,
                bin_seconds=bin_seconds,
                target_column=target_column,
                bucket_starts=segment_buckets,
                values=segment_values,
            )
        )
    if not result:
        raise ValueError(f"{source} 的序列 {series_id} 没有至少 2 点的连续片段")
    return result


def _load_aggregated_frame(
    frame: pd.DataFrame,
    source: Path,
    bin_seconds: int,
    target_column: str,
) -> list[FlowSeries]:
    if target_column not in frame.columns:
        raise ValueError(
            f"聚合数据 {source} 缺少目标列 {target_column}；可选列为 {', '.join(COUNT_COLUMNS)}"
        )
    if "bin_seconds" in frame.columns:
        declared_bins = pd.to_numeric(frame["bin_seconds"], errors="coerce")
        if declared_bins.isna().any() or set(declared_bins.astype(int)) != {bin_seconds}:
            raise ValueError(f"{source} 的 bin_seconds 与请求的 {bin_seconds} 秒不一致")

    working = frame.copy()
    if "series_id" not in working.columns:
        working["series_id"] = source.stem
    if working["series_id"].isna().any() or working["series_id"].astype(str).str.strip().eq("").any():
        raise ValueError(f"{source} 的 series_id 不能为空")

    values = pd.to_numeric(working[target_column], errors="coerce")
    if values.isna().any():
        raise ValueError(f"{source} 的目标列 {target_column} 包含非数值或空值")
    if (values < 0).any():
        raise ValueError(f"{source} 的目标列 {target_column} 不能包含负数")
    working[target_column] = values

    results: list[FlowSeries] = []
    for raw_series_id, group in working.groupby("series_id", sort=False):
        series_id = str(raw_series_id)
        if "bucket_start_seconds" in group.columns:
            buckets = pd.to_numeric(group["bucket_start_seconds"], errors="coerce")
            if buckets.isna().any() or not np.allclose(buckets, np.round(buckets)):
                raise ValueError(f"{source} 的序列 {series_id} 包含无效时间桶")
            bucket_starts = buckets.to_numpy(dtype=np.int64)
        else:
            bucket_starts = np.arange(len(group), dtype=np.int64) * bin_seconds
        results.extend(
            _split_contiguous_series(
                source=source,
                series_id=series_id,
                target_column=target_column,
                bin_seconds=bin_seconds,
                bucket_starts=bucket_starts,
                values=group[target_column].to_numpy(dtype=np.float32),
            )
        )
    return results


def _to_seconds(values: pd.Series, column: str) -> pd.Series:
    if column == "timestamp(ms)":
        return pd.to_numeric(values, errors="coerce") / 1000.0

    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().mean() >= 0.95:
        return numeric
    timestamps = pd.to_datetime(values, errors="coerce", utc=True)
    seconds = pd.Series(np.nan, index=values.index, dtype=np.float64)
    valid = timestamps.notna()
    seconds.loc[valid] = timestamps.loc[valid].astype("int64") / 1_000_000_000
    return seconds


def _trajectory_frames(source: Path) -> tuple[Iterable[pd.DataFrame], str, str]:
    if source.suffix.lower() == ".csv":
        header = pd.read_csv(source, nrows=0)
        time_column = _find_column(header.columns, TIME_COLUMNS)
        id_column = _find_column(header.columns, VEHICLE_ID_COLUMNS)
        if not time_column or not id_column:
            raise ValueError(
                f"{source} 必须包含轨迹字段 timestamp(ms)+track_id、Time+ID 或 time+vehicle_id"
            )
        frames = pd.read_csv(
            source,
            usecols=[time_column, id_column],
            chunksize=250_000,
        )
        return frames, time_column, id_column

    workbook = pd.ExcelFile(source)
    sheet_specs: list[tuple[str, str, str]] = []
    for sheet_name in workbook.sheet_names:
        header = workbook.parse(sheet_name=sheet_name, nrows=0)
        time_column = _find_column(header.columns, TIME_COLUMNS)
        id_column = _find_column(header.columns, VEHICLE_ID_COLUMNS)
        if time_column and id_column:
            sheet_specs.append((sheet_name, time_column, id_column))
    if not sheet_specs:
        raise ValueError(
            f"{source} 必须包含轨迹字段 timestamp(ms)+track_id、Time+ID 或 time+vehicle_id"
        )
    time_columns = {item[1] for item in sheet_specs}
    id_columns = {item[2] for item in sheet_specs}
    if len(time_columns) != 1 or len(id_columns) != 1:
        raise ValueError(f"{source} 各工作表的时间或车辆 ID 字段不一致")
    time_column = next(iter(time_columns))
    id_column = next(iter(id_columns))
    def iter_sheets() -> Iterable[pd.DataFrame]:
        try:
            for sheet_name, sheet_time, sheet_id in sheet_specs:
                yield workbook.parse(
                    sheet_name=sheet_name,
                    usecols=[sheet_time, sheet_id],
                )
        finally:
            workbook.close()

    frames = iter_sheets()
    return frames, time_column, id_column


def _load_trajectory_series(
    source: Path,
    bin_seconds: int,
    target_column: str,
    min_bucket_coverage_ratio: float,
) -> list[FlowSeries]:
    if target_column not in COUNT_COLUMNS:
        raise ValueError(f"轨迹数据不支持目标列 {target_column}")
    active_ids: dict[int, set[str]] = defaultdict(set)
    first_bucket_by_id: dict[str, int] = {}
    bucket_min: dict[int, float] = {}
    bucket_max: dict[int, float] = {}

    frames, time_column, id_column = _trajectory_frames(source)
    for frame in frames:
        seconds = _to_seconds(frame[time_column], time_column)
        ids = frame[id_column].astype("string")
        valid = seconds.notna() & ids.notna() & ids.str.strip().ne("")
        if not valid.any():
            continue
        compact = pd.DataFrame(
            {
                "seconds": seconds.loc[valid].astype(float),
                "vehicle_id": ids.loc[valid].astype(str),
            }
        )
        compact["bucket"] = np.floor(compact["seconds"] / bin_seconds).astype(np.int64)
        for bucket, group in compact.groupby("bucket", sort=False):
            bucket_value = int(bucket)
            active_ids[bucket_value].update(group["vehicle_id"])
            local_min = float(group["seconds"].min())
            local_max = float(group["seconds"].max())
            bucket_min[bucket_value] = min(bucket_min.get(bucket_value, local_min), local_min)
            bucket_max[bucket_value] = max(bucket_max.get(bucket_value, local_max), local_max)
        for vehicle_id, first_bucket in compact.groupby("vehicle_id")["bucket"].min().items():
            bucket_value = int(first_bucket)
            previous = first_bucket_by_id.get(vehicle_id)
            if previous is None or bucket_value < previous:
                first_bucket_by_id[vehicle_id] = bucket_value

    coverage_threshold = bin_seconds * min_bucket_coverage_ratio
    complete_buckets = sorted(
        bucket
        for bucket in active_ids
        if bucket_max[bucket] - bucket_min[bucket] >= coverage_threshold
    )
    if not complete_buckets:
        raise ValueError(f"{source} 没有覆盖至少 {coverage_threshold:g} 秒的完整时间桶")

    entering_counts = Counter(first_bucket_by_id.values())
    if target_column == "vehicle_count":
        values = np.asarray([len(active_ids[bucket]) for bucket in complete_buckets], dtype=np.float32)
    else:
        values = np.asarray([entering_counts[bucket] for bucket in complete_buckets], dtype=np.float32)
    return _split_contiguous_series(
        source=source,
        series_id=source.stem,
        target_column=target_column,
        bin_seconds=bin_seconds,
        bucket_starts=np.asarray(complete_buckets, dtype=np.int64) * bin_seconds,
        values=values,
    )


def load_flow_series_set(
    path: str | Path,
    bin_seconds: int = 60,
    target_column: str = "vehicle_count",
    min_bucket_coverage_ratio: float = 0.95,
) -> list[FlowSeries]:
    """Load one file as one or more independent continuous series."""

    if bin_seconds < 1:
        raise ValueError("bin_seconds 必须是正整数")
    if not 0 < min_bucket_coverage_ratio <= 1:
        raise ValueError("min_bucket_coverage_ratio 必须位于 (0, 1] 区间")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"训练数据不存在: {source}")
    if source.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
        raise ValueError("训练数据仅支持 .xlsx、.xls 或 .csv")

    if source.suffix.lower() == ".csv":
        header = pd.read_csv(source, nrows=0)
        if any(column in header.columns for column in COUNT_COLUMNS):
            return _load_aggregated_frame(
                pd.read_csv(source), source, bin_seconds, target_column
            )
    else:
        first_header = pd.read_excel(source, nrows=0)
        if any(column in first_header.columns for column in COUNT_COLUMNS):
            return _load_aggregated_frame(
                pd.read_excel(source), source, bin_seconds, target_column
            )
    return _load_trajectory_series(
        source,
        bin_seconds,
        target_column,
        min_bucket_coverage_ratio,
    )


def load_flow_series(
    path: str | Path,
    bin_seconds: int = 60,
    target_column: str = "vehicle_count",
) -> np.ndarray:
    """Backward-compatible loader for files containing exactly one series."""

    series_set = load_flow_series_set(path, bin_seconds, target_column)
    if len(series_set) != 1:
        raise ValueError(
            f"{Path(path)} 包含 {len(series_set)} 条独立序列；请使用 load_flow_series_set()"
        )
    return series_set[0].values


def _concat_window_sets(
    items: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.concatenate([item[0] for item in items], axis=0),
        np.concatenate([item[1] for item in items], axis=0),
    )


def prepare_window_partitions(
    series_set: list[FlowSeries],
    history_steps: int,
    forecast_steps: int,
    validation_ratio: float = 0.2,
    purge_steps: int = 0,
) -> WindowPartitions:
    """Create leakage-free train/validation windows across independent scenes."""

    _validate_steps(history_steps, forecast_steps)
    if not 0 < validation_ratio < 1:
        raise ValueError("validation_ratio 必须位于 (0, 1) 区间")
    if purge_steps < 0:
        raise ValueError("purge_steps 不能为负数")

    minimum_points = history_steps + forecast_steps
    usable: list[tuple[FlowSeries, int]] = []
    skipped: list[dict[str, object]] = []
    for item in series_set:
        window_count = len(item.values) - minimum_points + 1
        if window_count < 1:
            skipped.append(
                {
                    "series": item.key,
                    "actual_points": len(item.values),
                    "required_points": minimum_points,
                }
            )
            continue
        usable.append((item, window_count))
    if not usable:
        raise ValueError(
            f"所有场景均不足以构窗；每条连续序列至少需要 {minimum_points} 个时间点"
        )

    if len(usable) >= 2:
        # 多场景时整条留出验证，彻底隔离相邻滑窗与同一车辆轨迹。
        total_windows = sum(window_count for _, window_count in usable)
        target_validation_windows = max(1, round(total_windows * validation_ratio))
        validation_index = min(
            range(len(usable)),
            key=lambda index: (
                abs(usable[index][1] - target_validation_windows),
                usable[index][1],
                index,
            ),
        )
        validation_item = usable[validation_index][0]
        training_items = [item for index, (item, _) in enumerate(usable) if index != validation_index]
        train_windows = [
            build_windows(item.values, history_steps, forecast_steps) for item in training_items
        ]
        validation_windows = [
            build_windows(validation_item.values, history_steps, forecast_steps)
        ]
        train_features, train_targets = _concat_window_sets(train_windows)
        validation_features, validation_targets = _concat_window_sets(validation_windows)
        scaler_values = np.concatenate([item.values for item in training_items])
        split_strategy = "series_holdout"
        training_series = tuple(item.key for item in training_items)
        validation_series = (validation_item.key,)
    else:
        # 单场景只能使用互不重叠的原始时间段，purge_steps 可额外留出缓冲区。
        item = usable[0][0]
        validation_points = max(minimum_points, round(len(item.values) * validation_ratio))
        train_end = len(item.values) - validation_points - purge_steps
        if train_end < minimum_points:
            required = minimum_points * 2 + purge_steps
            raise ValueError(
                f"单序列 {item.key} 无法无泄漏切分训练和验证；当前 {len(item.values)} 点，"
                f"至少需要 {required} 点"
            )
        train_values = item.values[:train_end]
        validation_values = item.values[train_end + purge_steps:]
        train_features, train_targets = build_windows(
            train_values, history_steps, forecast_steps
        )
        validation_features, validation_targets = build_windows(
            validation_values, history_steps, forecast_steps
        )
        scaler_values = train_values
        split_strategy = "temporal_holdout"
        training_series = (f"{item.key}:train",)
        validation_series = (f"{item.key}:validation",)

    return WindowPartitions(
        train_features=train_features,
        train_targets=train_targets,
        validation_features=validation_features,
        validation_targets=validation_targets,
        scaler_values=np.asarray(scaler_values, dtype=np.float32),
        split_strategy=split_strategy,
        training_series=training_series,
        validation_series=validation_series,
        skipped_series=tuple(skipped),
    )


def _split_teacher_contiguous_series(
    *,
    source: Path,
    series_id: str,
    bin_seconds: int,
    feature_columns: tuple[str, ...],
    target_column: str,
    bucket_starts: np.ndarray,
    features: np.ndarray,
    targets: np.ndarray,
) -> list[TeacherCompatSeries]:
    order = np.argsort(bucket_starts, kind="stable")
    ordered_buckets = np.asarray(bucket_starts, dtype=np.int64)[order]
    ordered_features = np.asarray(features, dtype=np.float32)[order]
    ordered_targets = np.asarray(targets, dtype=np.float32)[order]
    if len(np.unique(ordered_buckets)) != len(ordered_buckets):
        raise ValueError(f"{source} 的序列 {series_id} 存在重复时间桶")

    breaks = np.flatnonzero(np.diff(ordered_buckets) != bin_seconds) + 1
    bucket_segments = np.split(ordered_buckets, breaks)
    feature_segments = np.split(ordered_features, breaks)
    target_segments = np.split(ordered_targets, breaks)
    result: list[TeacherCompatSeries] = []
    for index, (segment_buckets, segment_features, segment_targets) in enumerate(
        zip(bucket_segments, feature_segments, target_segments, strict=True), start=1
    ):
        if len(segment_buckets) < 2:
            continue
        segment_id = series_id if len(bucket_segments) == 1 else f"{series_id}#segment{index}"
        result.append(
            TeacherCompatSeries(
                key=f"{source}:{segment_id}",
                series_id=segment_id,
                source_path=source,
                bin_seconds=bin_seconds,
                feature_columns=feature_columns,
                target_column=target_column,
                bucket_starts=segment_buckets,
                features=segment_features,
                targets=segment_targets,
            )
        )
    if not result:
        raise ValueError(f"{source} 的序列 {series_id} 没有至少 2 点的连续片段")
    return result


def load_teacher_compat_series(
    path: str | Path,
    *,
    feature_columns: tuple[str, ...] = TEACHER_FEATURE_COLUMNS,
    target_column: str = "vehicle_count",
    bin_seconds: int = 3600,
    holiday_policy: str = "source_holiday_or_weekend",
) -> list[TeacherCompatSeries]:
    """Load timestamped traffic CSV data as independent multivariate series."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"训练数据不存在: {source}")
    if source.suffix.lower() != ".csv":
        raise ValueError("teacher_compat 数据当前仅支持 CSV")
    if bin_seconds < 1:
        raise ValueError("bin_seconds 必须是正整数")
    unknown_features = set(feature_columns) - SUPPORTED_CALENDAR_FEATURE_COLUMNS
    if unknown_features or "vehicle_count" not in feature_columns:
        raise ValueError(
            "时间特征仅支持 "
            f"{tuple(sorted(SUPPORTED_CALENDAR_FEATURE_COLUMNS))}，"
            f"收到未知列 {tuple(sorted(unknown_features))}"
        )
    if target_column != "vehicle_count":
        raise ValueError("teacher_compat 当前仅支持 vehicle_count 预测目标")
    if holiday_policy not in {
        "source_holiday_or_weekend",
        "china_statutory_with_makeup",
    }:
        raise ValueError("holiday_policy 不受支持")

    frame = pd.read_csv(source)
    time_column = "date_time" if "date_time" in frame.columns else "timestamp"
    if time_column not in frame.columns:
        raise ValueError(f"{source} 缺少时间列 date_time 或 timestamp")
    volume_column = "vehicle_count" if "vehicle_count" in frame.columns else "traffic_volume"
    if volume_column not in frame.columns:
        raise ValueError(f"{source} 缺少 traffic_volume 或 vehicle_count")

    working = pd.DataFrame(index=frame.index)
    working["date_time"] = pd.to_datetime(frame[time_column], errors="coerce")
    working["vehicle_count"] = pd.to_numeric(frame[volume_column], errors="coerce")
    working["series_id"] = (
        frame["series_id"].astype("string")
        if "series_id" in frame.columns
        else pd.Series(source.stem, index=frame.index, dtype="string")
    )
    if working[["date_time", "vehicle_count", "series_id"]].isna().any().any():
        raise ValueError(f"{source} 包含无效 date_time、流量或 series_id")
    if working["series_id"].str.strip().eq("").any():
        raise ValueError(f"{source} 的 series_id 不能为空")
    if (working["vehicle_count"] < 0).any():
        raise ValueError(f"{source} 的 vehicle_count 不能包含负数")

    duplicate_groups = working.groupby(["series_id", "date_time"])["vehicle_count"].nunique()
    if (duplicate_groups > 1).any():
        raise ValueError(f"{source} 的相同 series_id/date_time 存在冲突流量")
    working = working.drop_duplicates(["series_id", "date_time"], keep="first")

    if "holiday" in frame.columns:
        raw_holiday = frame["holiday"].astype("string").fillna("None")
        raw_times = pd.to_datetime(frame[time_column], errors="coerce")
        holiday_dates = set(
            raw_times.loc[raw_holiday.str.strip().str.casefold().ne("none")]
            .dt.normalize()
            .dropna()
            .tolist()
        )
        named_holiday = working["date_time"].dt.normalize().isin(holiday_dates)
    elif "is_holiday" in frame.columns:
        numeric_holiday = pd.to_numeric(frame.loc[working.index, "is_holiday"], errors="coerce")
        if numeric_holiday.isna().any() or not numeric_holiday.isin([0, 1]).all():
            raise ValueError(f"{source} 的 is_holiday 必须为 0 或 1")
        named_holiday = numeric_holiday.astype(bool)
    else:
        named_holiday = pd.Series(False, index=working.index)

    working["hour"] = working["date_time"].dt.hour
    working["minute"] = working["date_time"].dt.minute
    working["day_of_week"] = working["date_time"].dt.dayofweek
    if holiday_policy == "source_holiday_or_weekend":
        working["is_holiday"] = (
            named_holiday.to_numpy(dtype=bool)
            | working["day_of_week"].ge(5).to_numpy(dtype=bool)
        ).astype(np.float32)
        working["is_makeup_workday"] = np.zeros(len(working), dtype=np.float32)
    else:
        if "is_holiday" not in frame.columns or "is_makeup_workday" not in frame.columns:
            raise ValueError(
                f"{source} 使用中国调休日历时必须包含 is_holiday 和 is_makeup_workday"
            )
        makeup_workday = pd.to_numeric(
            frame.loc[working.index, "is_makeup_workday"], errors="coerce"
        )
        if makeup_workday.isna().any() or not makeup_workday.isin([0, 1]).all():
            raise ValueError(f"{source} 的 is_makeup_workday 必须为 0 或 1")
        if (named_holiday.to_numpy(dtype=bool) & makeup_workday.astype(bool)).any():
            raise ValueError(f"{source} 的同一日期不能同时是法定假日和调休工作日")
        working["is_holiday"] = named_holiday.to_numpy(dtype=np.float32)
        working["is_makeup_workday"] = makeup_workday.to_numpy(dtype=np.float32)
    working["bucket_start_seconds"] = working["date_time"].to_numpy(
        dtype="datetime64[s]"
    ).astype(np.int64)

    results: list[TeacherCompatSeries] = []
    for raw_series_id, group in working.groupby("series_id", sort=False):
        series_id = str(raw_series_id)
        results.extend(
            _split_teacher_contiguous_series(
                source=source,
                series_id=series_id,
                bin_seconds=bin_seconds,
                feature_columns=tuple(feature_columns),
                target_column=target_column,
                bucket_starts=group["bucket_start_seconds"].to_numpy(dtype=np.int64),
                features=group[list(feature_columns)].to_numpy(dtype=np.float32),
                targets=group[target_column].to_numpy(dtype=np.float32),
            )
        )
    return results


def _teacher_windows(
    item: TeacherCompatSeries,
    start: int,
    stop: int,
    history_steps: int,
) -> TeacherWindowSet:
    features = item.features[start:stop]
    targets = item.targets[start:stop]
    times = item.bucket_starts[start:stop]
    window_count = len(features) - history_steps
    window_features = np.stack(
        [features[index:index + history_steps] for index in range(window_count)]
    )
    window_targets = np.asarray(
        [targets[index + history_steps] for index in range(window_count)],
        dtype=np.float32,
    ).reshape(-1, 1)
    feature_times = np.stack(
        [times[index:index + history_steps] for index in range(window_count)]
    )
    target_times = np.asarray(
        [times[index + history_steps] for index in range(window_count)],
        dtype=np.int64,
    )
    return TeacherWindowSet(
        features=window_features.astype(np.float32, copy=False),
        targets=window_targets,
        feature_times=feature_times.astype(np.int64, copy=False),
        target_times=target_times,
        series_keys=(item.key,) * window_count,
    )


def _concat_teacher_window_sets(items: list[TeacherWindowSet]) -> TeacherWindowSet:
    return TeacherWindowSet(
        features=np.concatenate([item.features for item in items], axis=0),
        targets=np.concatenate([item.targets for item in items], axis=0),
        feature_times=np.concatenate([item.feature_times for item in items], axis=0),
        target_times=np.concatenate([item.target_times for item in items], axis=0),
        series_keys=tuple(key for item in items for key in item.series_keys),
    )


def prepare_teacher_compat_partitions(
    series_set: list[TeacherCompatSeries],
    *,
    history_steps: int = 6,
    validation_ratio: float = 0.15,
    test_ratio: float = 0.15,
    purge_steps: int = 0,
) -> TeacherWindowPartitions:
    """Split the global raw timeline first, then build one-step windows."""

    if history_steps < 1:
        raise ValueError("history_steps 必须是正整数")
    if validation_ratio <= 0 or test_ratio <= 0 or validation_ratio + test_ratio >= 1:
        raise ValueError("validation_ratio 和 test_ratio 必须为正且总和小于 1")
    if purge_steps < 0:
        raise ValueError("purge_steps 不能为负数")
    if not series_set:
        raise ValueError("teacher_compat 至少需要一条序列")

    feature_columns = series_set[0].feature_columns
    target_column = series_set[0].target_column
    minimum_points = history_steps + 1
    train_sets: list[TeacherWindowSet] = []
    validation_sets: list[TeacherWindowSet] = []
    test_sets: list[TeacherWindowSet] = []
    scaler_values: list[np.ndarray] = []
    skipped: list[dict[str, object]] = []

    unique_times = np.unique(
        np.concatenate([item.bucket_starts for item in series_set]).astype(np.int64)
    )
    if len(unique_times) < minimum_points * 3 + 2 * purge_steps:
        raise ValueError(
            "teacher_compat 全局时间轴不足以切分 train/validation/test；"
            f"至少需要 {minimum_points * 3 + 2 * purge_steps} 个时间点"
        )
    train_ratio = 1.0 - validation_ratio - test_ratio
    train_time_count = max(1, int(np.floor(len(unique_times) * train_ratio)))
    validation_time_count = max(1, int(np.floor(len(unique_times) * validation_ratio)))
    validation_end_index = min(
        train_time_count + validation_time_count - 1,
        len(unique_times) - 2,
    )
    train_end_time = int(unique_times[train_time_count - 1])
    validation_end_time = int(unique_times[validation_end_index])

    for item in series_set:
        if item.feature_columns != feature_columns or item.target_column != target_column:
            raise ValueError("所有 teacher_compat 序列必须使用相同特征与目标契约")
        purge_seconds = purge_steps * item.bin_seconds
        masks = {
            "train": item.bucket_starts <= train_end_time,
            "validation": (
                (item.bucket_starts > train_end_time + purge_seconds)
                & (item.bucket_starts <= validation_end_time)
            ),
            "test": item.bucket_starts > validation_end_time + purge_seconds,
        }
        for partition_name, mask in masks.items():
            indices = np.flatnonzero(mask)
            if partition_name == "train" and len(indices):
                scaler_values.append(item.features[indices])
            if len(indices) < minimum_points:
                if len(indices):
                    skipped.append(
                        {
                            "series": item.key,
                            "partition": partition_name,
                            "actual_points": len(indices),
                            "required_points": minimum_points,
                        }
                    )
                continue
            window_set = _teacher_windows(
                item,
                int(indices[0]),
                int(indices[-1]) + 1,
                history_steps,
            )
            if partition_name == "train":
                train_sets.append(window_set)
            elif partition_name == "validation":
                validation_sets.append(window_set)
            else:
                test_sets.append(window_set)

    if not train_sets or not validation_sets or not test_sets or not scaler_values:
        raise ValueError("全局时间切分后至少一个 partition 没有可用窗口")
    return TeacherWindowPartitions(
        train=_concat_teacher_window_sets(train_sets),
        validation=_concat_teacher_window_sets(validation_sets),
        test=_concat_teacher_window_sets(test_sets),
        scaler_values=np.concatenate(scaler_values, axis=0).astype(np.float32, copy=False),
        feature_columns=feature_columns,
        target_column=target_column,
        skipped_series=tuple(skipped),
    )
