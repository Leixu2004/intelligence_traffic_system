"""Data access and offline fallback helpers for the Streamlit dashboard.

The dashboard is intentionally read-only. It first tries the existing FastAPI
endpoint, then falls back to local CSV/Parquet assets, and finally creates a
small deterministic sample set so a cold checkout still renders a useful UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover - requests is listed in dashboard requirements
    requests = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INNER_ROOT = PROJECT_ROOT / "intel-transportation"
DEFAULT_API_URL = os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8000")
PREDICTION_API_KEY = os.getenv("PREDICTION_API_KEY", "").strip()


def _prediction_headers() -> dict[str, str]:
    return {"X-API-Key": PREDICTION_API_KEY} if PREDICTION_API_KEY else {}

# --- 地图监测区域：广州天河 -------------------------------------------------
# 大屏地图的范围完全由卡口经纬度决定，所以换区域就是换卡口坐标落点。
# AREA=tianhe 时把所有数据源的卡口重映射到天河区真实地标；只改经纬度，
# 车流量和速度沿用数据源原值——这是演示区域化，不等于天河区真实现场采集。
# 需要看真实路网坐标时设 DASHBOARD_MAP_AREA=source。
MAP_AREA = os.getenv("DASHBOARD_MAP_AREA", "tianhe").strip().lower()
AREA_LABEL = "广州天河" if MAP_AREA == "tianhe" else "数据源原始坐标"
# 天河区政府一带，作为无坐标数据时的地图兜底中心。
AREA_CENTER = (113.3276, 23.1297)

# 地标名 + 经纬度（近似值，用于大屏标注与卡口轮转分配）。
TIANHE_LANDMARKS: tuple[tuple[str, float, float], ...] = (
    ("天河路·体育西路", 113.3252, 23.1330),
    ("珠江新城·花城广场", 113.3218, 23.1195),
    ("天河北路·龙口西", 113.3312, 23.1412),
    ("黄埔大道西·岗顶", 113.3352, 23.1268),
    ("中山大道西·棠下", 113.3601, 23.1233),
    ("科韵路·天河段", 113.3488, 23.1278),
    ("华南快速·新塘立交", 113.3690, 23.1345),
    ("广园快速·瘦狗岭", 113.3330, 23.1520),
    ("燕岭路·京溪", 113.3435, 23.1635),
    ("龙洞·华南植物园", 113.3905, 23.1880),
)

# 已知卡口的固定落点，保证每次刷新同一卡口画在同一位置。
CP_TIANHE_COORDS: dict[str, tuple[float, float]] = {
    "CP-NORTH-01": (113.3312, 23.1412),
    "CP-NORTH-02": (113.3435, 23.1635),
    "CP-NORTH-03": (113.3330, 23.1520),
    "CP-EAST-05": (113.3690, 23.1345),
    "CP-EAST-06": (113.3905, 23.1880),
    "CP-SOUTH-02": (113.3218, 23.1195),
    "CP-SOUTH-03": (113.3488, 23.1278),
    "CP-WEST-02": (113.3352, 23.1268),
    "KDD-T1-D0": (113.3252, 23.1330),
}

CHECKPOINT_DEFAULTS = dict(CP_TIANHE_COORDS)


@dataclass(frozen=True)
class DashboardBundle:
    traffic: pd.DataFrame
    detections: pd.DataFrame
    source: str
    warning: str = ""
    loaded_at: str = ""


@dataclass(frozen=True)
class RemotePrediction:
    values: list[float] | None
    interval: pd.Timedelta
    model: str
    error: str = ""


def _area_coordinates(checkpoint_ids: list[str]) -> dict[str, tuple[float, float]]:
    """给每个卡口定一个天河坐标；未登记过的卡口按 ID 排序轮转分配地标。"""
    known = {cid: CP_TIANHE_COORDS[cid] for cid in checkpoint_ids if cid in CP_TIANHE_COORDS}
    unknown = sorted(cid for cid in checkpoint_ids if cid not in CP_TIANHE_COORDS)
    spread = {
        cid: TIANHE_LANDMARKS[(index + len(known)) % len(TIANHE_LANDMARKS)][1:3]
        for index, cid in enumerate(unknown)
    }
    return {**known, **spread}


def _remap_to_area(result: pd.DataFrame) -> pd.DataFrame:
    if MAP_AREA != "tianhe" or result.empty:
        return result
    coords = _area_coordinates(sorted(result["checkpoint_id"].astype(str).unique()))
    result["gps_lng"] = result["checkpoint_id"].map(lambda cid: coords[str(cid)][0])
    result["gps_lat"] = result["checkpoint_id"].map(lambda cid: coords[str(cid)][1])
    return result


def _normalise_traffic(df: pd.DataFrame) -> pd.DataFrame:
    """Return traffic records with one stable schema across all sources."""
    if df.empty:
        return pd.DataFrame(
            columns=[
                "time",
                "vehicle_id",
                "checkpoint_id",
                "gps_lng",
                "gps_lat",
                "speed_kmh",
                "vehicle_count",
            ]
        )

    aliases = {
        "timestamp": "time",
        "bucket": "time",
        "checkpoint": "checkpoint_id",
        "vehicle_count": "vehicle_count",
        "average_speed": "speed_kmh",
    }
    result = df.rename(columns={k: v for k, v in aliases.items() if k in df.columns}).copy()
    for column in ("time", "vehicle_id", "checkpoint_id", "gps_lng", "gps_lat", "speed_kmh"):
        if column not in result.columns:
            result[column] = np.nan

    result["time"] = pd.to_datetime(result["time"], errors="coerce", utc=True)
    result["checkpoint_id"] = result["checkpoint_id"].fillna("UNKNOWN").astype(str)
    result["vehicle_id"] = result["vehicle_id"].fillna(
        pd.Series([f"ROW-{i}" for i in range(len(result))], index=result.index)
    ).astype(str)
    for column in ("gps_lng", "gps_lat", "speed_kmh"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if "vehicle_count" not in result.columns:
        result["vehicle_count"] = 1
    result["vehicle_count"] = pd.to_numeric(result["vehicle_count"], errors="coerce").fillna(1).clip(lower=0)

    result = result.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    result = _remap_to_area(result)
    return result[
        ["time", "vehicle_id", "checkpoint_id", "gps_lng", "gps_lat", "speed_kmh", "vehicle_count"]
    ]


def _normalise_detections(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "time", "camera_id", "checkpoint_id", "plate", "vehicle_type",
                "confidence", "violation_type", "image_path",
            ]
        )

    result = df.copy()
    result["time"] = pd.to_datetime(result.get("time"), errors="coerce", utc=True)
    for column in ("camera_id", "checkpoint_id", "plate", "vehicle_type", "violation_type", "image_path"):
        if column not in result.columns:
            result[column] = "UNKNOWN"
        result[column] = result[column].fillna("UNKNOWN").astype(str)
    if "confidence" not in result.columns:
        result["confidence"] = np.nan
    result["confidence"] = pd.to_numeric(result["confidence"], errors="coerce")
    return result.sort_values("time", ascending=False).reset_index(drop=True)


def _read_first_csv(candidates: list[Path], normaliser) -> tuple[pd.DataFrame, Path | None]:
    for path in candidates:
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path)
            normalised = normaliser(frame)
            if not normalised.empty:
                return normalised, path
        except (OSError, ValueError, UnicodeError):
            continue
    return normaliser(pd.DataFrame()), None


def _load_api_traffic(api_url: str, timeout: float = 2.0) -> tuple[pd.DataFrame, str]:
    if requests is None:
        raise RuntimeError("requests is not installed")
    endpoint = f"{api_url.rstrip('/')}/api/traffic_trend"
    response = requests.get(endpoint, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("message", "dashboard API returned an error"))
    return _normalise_traffic(pd.DataFrame(payload.get("data", []))), str(payload.get("source", "FastAPI"))


def _load_api_detections(api_url: str, timeout: float = 2.0) -> pd.DataFrame:
    if requests is None:
        raise RuntimeError("requests is not installed")
    response = requests.get(f"{api_url.rstrip('/')}/api/detections", timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("message", "dashboard API returned an error"))
    return _normalise_detections(pd.DataFrame(payload.get("data", [])))


def _synthetic_traffic() -> pd.DataFrame:
    """Create deterministic data for the no-database/no-file startup path."""
    rng = np.random.default_rng(20260910)
    end = pd.Timestamp.now(tz="UTC").floor("min")
    times = pd.date_range(end=end, periods=24 * 4, freq="15min", tz="UTC")
    records: list[dict[str, Any]] = []
    sequence = 1
    checkpoints = list(CHECKPOINT_DEFAULTS.items())
    for slot, timestamp in enumerate(times):
        rush_factor = 1.0 + 0.8 * (6 <= timestamp.hour <= 9) + 0.9 * (17 <= timestamp.hour <= 19)
        for cp_index, (checkpoint, (lng, lat)) in enumerate(checkpoints):
            count = max(3, int(rng.normal(9 * rush_factor, 2)))
            for offset in range(count):
                records.append(
                    {
                        "time": timestamp + pd.Timedelta(seconds=offset * 20),
                        "vehicle_id": f"SIM-{sequence:05d}",
                        "checkpoint_id": checkpoint,
                        "gps_lng": lng + rng.normal(0, 0.0007),
                        "gps_lat": lat + rng.normal(0, 0.0007),
                        "speed_kmh": round(float(rng.normal(43 - 7 * (rush_factor > 1.5), 6)), 1),
                    }
                )
                sequence += 1
    return _normalise_traffic(pd.DataFrame(records))


def load_traffic_data(source_mode: str = "auto", api_url: str = DEFAULT_API_URL) -> DashboardBundle:
    """Load traffic records from API or local assets with a safe fallback."""
    warnings: list[str] = []
    source_mode = source_mode.lower()

    if source_mode in {"auto", "api"}:
        try:
            api_df, api_source = _load_api_traffic(api_url)
            if not api_df.empty:
                try:
                    detections = _load_api_detections(api_url)
                except Exception as exc:
                    detections = load_detection_data()
                    warnings.append(f"实时识别记录不可用，使用本地记录：{exc}")
                return DashboardBundle(
                    traffic=api_df,
                    detections=detections,
                    source=f"FastAPI / {api_source}",
                    warning="；".join(warnings),
                    loaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
        except Exception as exc:  # API availability must never break the screen
            warnings.append(f"实时 API 不可用，已切换本地数据：{exc}")
            if source_mode == "api":
                warnings.append("API 模式请求失败，继续使用本地兜底以保持页面可用")

    local_candidates = [
        INNER_ROOT / "data" / "vehicle_records.csv",
        PROJECT_ROOT / "vehicle_records.csv",
    ]
    local_df, local_path = _read_first_csv(local_candidates, _normalise_traffic)
    if not local_df.empty:
        return DashboardBundle(
            traffic=local_df,
            detections=load_detection_data(),
            source=f"本地 CSV: {local_path.name if local_path else 'unknown'}",
            warning="；".join(warnings),
            loaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    parquet_path = INNER_ROOT / "data" / "traffic_archive.parquet"
    if parquet_path.exists():
        try:
            parquet_df = _normalise_traffic(pd.read_parquet(parquet_path))
            if not parquet_df.empty:
                return DashboardBundle(
                    traffic=parquet_df,
                    detections=load_detection_data(),
                    source="本地 Parquet 归档",
                    warning="；".join(warnings),
                    loaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
        except Exception as exc:
            warnings.append(f"Parquet 读取失败：{exc}")

    return DashboardBundle(
        traffic=_synthetic_traffic(),
        detections=load_detection_data(),
        source="内置示例数据",
        warning="；".join(warnings + ["未找到可用外部数据，当前展示可复现的示例流量"]),
        loaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def load_detection_data() -> pd.DataFrame:
    candidates = [PROJECT_ROOT / "detections.csv", INNER_ROOT / "data" / "detections.csv"]
    frame, _ = _read_first_csv(candidates, _normalise_detections)
    return frame


def filter_traffic(traffic: pd.DataFrame, checkpoint: str = "全部卡口") -> pd.DataFrame:
    if checkpoint == "全部卡口" or "checkpoint_id" not in traffic.columns:
        return traffic.copy()
    return traffic.loc[traffic["checkpoint_id"].eq(checkpoint)].copy()


def aggregate_traffic(traffic: pd.DataFrame, frequency: str = "15min") -> pd.DataFrame:
    """Aggregate raw or API records into chart-friendly traffic buckets."""
    if traffic.empty:
        return pd.DataFrame(columns=["time", "vehicle_count", "average_speed", "checkpoint_id"])

    frame = traffic.copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["time"]).set_index("time")
    grouped = (
        frame.groupby([pd.Grouper(freq=frequency), "checkpoint_id"], observed=True)
        .agg(
            vehicle_count=("vehicle_count", "sum"),
            average_speed=("speed_kmh", "mean"),
        )
        .reset_index()
        .rename(columns={"time": "time"})
    )
    grouped["average_speed"] = grouped["average_speed"].round(1)
    return grouped.sort_values("time").reset_index(drop=True)


def _calendar_feature_row(
    timestamp: pd.Timestamp,
    volume: float,
    feature_columns: list[str],
    timezone_name: str,
    model_info: dict[str, Any] | None = None,
) -> list[float] | None:
    try:
        local_time = timestamp.tz_convert(timezone_name)
    except (TypeError, ValueError, KeyError):
        return None
    info = model_info or {}
    holiday_policy = str(info.get("holiday_policy") or "source_holiday_or_weekend")
    if holiday_policy == "china_statutory_with_makeup":
        local_date = local_time.date()
        periods = info.get("inference_calendar_periods") or []
        period = None
        if isinstance(periods, list):
            for candidate in periods:
                if not isinstance(candidate, dict):
                    continue
                valid_from = pd.to_datetime(candidate.get("valid_from"), errors="coerce")
                valid_to = pd.to_datetime(candidate.get("valid_to"), errors="coerce")
                if (
                    not pd.isna(valid_from)
                    and not pd.isna(valid_to)
                    and valid_from.date() <= local_date <= valid_to.date()
                ):
                    period = candidate
                    break
        if period is None:
            valid_from = pd.to_datetime(
                info.get("holiday_calendar_valid_from"), errors="coerce"
            )
            valid_to = pd.to_datetime(
                info.get("holiday_calendar_valid_to"), errors="coerce"
            )
            if (
                pd.isna(valid_from)
                or pd.isna(valid_to)
                or local_date < valid_from.date()
                or local_date > valid_to.date()
            ):
                return None
            period = info
        date_key = local_date.isoformat()
        is_holiday = float(date_key in set(period.get("holiday_dates") or []))
        is_makeup_workday = float(date_key in set(period.get("makeup_workdays") or []))
    else:
        is_holiday = float(local_time.dayofweek >= 5)
        is_makeup_workday = 0.0
    values = {
        "vehicle_count": float(volume),
        "hour": float(local_time.hour),
        "minute": float(local_time.minute),
        "day_of_week": float(local_time.dayofweek),
        "is_holiday": is_holiday,
        "is_makeup_workday": is_makeup_workday,
    }
    if any(column not in values for column in feature_columns):
        return None
    return [values[column] for column in feature_columns]


def _remote_prediction(
    api_url: str,
    history: pd.DataFrame,
    steps: int,
    frequency: str,
) -> RemotePrediction:
    default_interval = pd.Timedelta(frequency)
    if requests is None:
        return RemotePrediction(None, default_interval, "趋势兜底", "requests 未安装")
    if history.empty:
        return RemotePrediction(None, default_interval, "趋势兜底", "没有可用历史流量")
    try:
        base_url = api_url.rstrip("/")
        info_response = requests.get(f"{base_url}/model/info", timeout=2.0)
        info_response.raise_for_status()
        info_payload = info_response.json()
        info = info_payload.get("data", info_payload)
        feature_columns = list(info.get("feature_columns") or ["vehicle_count"])
        history_steps = int(info.get("history_steps") or 0)
        model_bin_seconds = int(info.get("bin_seconds") or pd.Timedelta(frequency).total_seconds())
        model_forecast_steps = int(info.get("forecast_steps") or 15)
        target_column = str(info.get("target_column") or "vehicle_count")
        model_name = str(info.get("model") or "FastAPI / ONNX")
        model_interval = pd.Timedelta(seconds=model_bin_seconds)

        frame = history.copy()
        frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
        frame["vehicle_count"] = pd.to_numeric(
            frame.get("vehicle_count"), errors="coerce"
        )
        frame = frame.dropna(subset=["time", "vehicle_count"])
        if frame.empty:
            return RemotePrediction(None, model_interval, "趋势兜底", "没有有效历史流量")
        if (
            str(info.get("model_type") or "") == "lstm"
            and "checkpoint_id" in frame.columns
            and frame["checkpoint_id"].astype(str).nunique() != 1
        ):
            return RemotePrediction(
                None,
                model_interval,
                "趋势兜底",
                "LSTM 仅支持单卡口序列，请先选择一个卡口",
            )

        model_series = (
            frame.set_index("time")["vehicle_count"]
            .resample(f"{model_bin_seconds}s")
            .sum(min_count=1)
            .sort_index()
        )
        missing_bucket_policy = str(info.get("missing_bucket_policy") or "zero_fill")
        if missing_bucket_policy == "split_on_gap" and model_series.isna().any():
            last_gap_position = int(np.flatnonzero(model_series.isna().to_numpy())[-1])
            model_series = model_series.iloc[last_gap_position + 1 :]
        elif missing_bucket_policy == "zero_fill":
            model_series = model_series.fillna(0.0)
        elif model_series.isna().any():
            return RemotePrediction(
                None,
                model_interval,
                "趋势兜底",
                f"不支持的缺失时间桶策略: {missing_bucket_policy}",
            )
        model_series = model_series.dropna()

        if len(feature_columns) == 1:
            values = model_series.astype(float).tolist()
            if history_steps and len(values) < history_steps:
                return RemotePrediction(
                    None,
                    model_interval,
                    "趋势兜底",
                    f"模型需要 {history_steps} 个历史点，当前只有 {len(values)} 个",
                )
            if history_steps:
                values = values[-history_steps:]
            response = requests.post(
                f"{base_url}/api/v1/predict/traffic-flow",
                headers=_prediction_headers(),
                json={
                    "features": [values],
                    "future_steps": min(steps, model_forecast_steps, 15),
                    "moving_average_window": 5,
                    "time_step_seconds": int(pd.Timedelta(frequency).total_seconds()),
                    "target_column": target_column,
                },
                timeout=2.0,
            )
            response.raise_for_status()
            forecast = response.json().get("data", {}).get("forecast")
            if isinstance(forecast, list) and len(forecast) >= steps:
                return RemotePrediction(
                    [float(value) for value in forecast[:steps]],
                    model_interval,
                    model_name,
                )
            return RemotePrediction(None, model_interval, "趋势兜底", "模型返回的预测步数不足")

        if history_steps < 2 or len(model_series) < history_steps:
            return RemotePrediction(
                None,
                model_interval,
                "趋势兜底",
                f"模型需要 {history_steps} 个历史点，当前只有 {len(model_series)} 个",
            )

        timezone_name = str(info.get("timezone") or "UTC")
        forecast_values: list[float] = []
        for _ in range(steps):
            recent = model_series.tail(history_steps)
            feature_rows = [
                _calendar_feature_row(
                    timestamp,
                    value,
                    feature_columns,
                    timezone_name,
                    info,
                )
                for timestamp, value in recent.items()
            ]
            if any(row is None for row in feature_rows):
                return RemotePrediction(
                    None,
                    model_interval,
                    "趋势兜底",
                    "模型日历不覆盖当前历史或预测日期",
                )
            response = requests.post(
                f"{base_url}/api/v1/predict/traffic-flow",
                headers=_prediction_headers(),
                json={
                    "features": feature_rows,
                    "future_steps": 1,
                    "moving_average_window": 5,
                    "time_step_seconds": model_bin_seconds,
                    "target_column": target_column,
                },
                timeout=2.0,
            )
            response.raise_for_status()
            forecast = response.json().get("data", {}).get("forecast")
            if not isinstance(forecast, list) or not forecast:
                return RemotePrediction(None, model_interval, "趋势兜底", "模型未返回预测值")
            prediction = float(forecast[0])
            forecast_values.append(prediction)
            model_series.loc[model_series.index[-1] + model_interval] = prediction
        return RemotePrediction(forecast_values, model_interval, model_name)
    except (OSError, ValueError, TypeError, requests.RequestException) as exc:
        return RemotePrediction(None, default_interval, "趋势兜底", str(exc))


def build_prediction_frame(
    traffic: pd.DataFrame,
    frequency: str = "15min",
    steps: int = 4,
    api_url: str | None = None,
) -> pd.DataFrame:
    trend = aggregate_traffic(traffic, frequency)
    if trend.empty:
        return trend.assign(kind=pd.Series(dtype=str))

    history = trend.groupby("time", as_index=False).agg(
        vehicle_count=("vehicle_count", "sum"), average_speed=("average_speed", "mean")
    )
    history["kind"] = "历史"
    if len(history) < 3:
        return history

    history_values = history["vehicle_count"].to_numpy(dtype=float)
    remote_result = (
        _remote_prediction(api_url, traffic, steps, frequency)
        if api_url
        else RemotePrediction(None, pd.Timedelta(frequency), "趋势兜底", "未配置预测 API")
    )
    if remote_result.values is None:
        x = np.arange(len(history), dtype=float)
        slope, intercept = np.polyfit(x, history_values, 1)
        future_values = np.maximum(0, np.round(intercept + slope * np.arange(len(history), len(history) + steps), 1))
        future_delta = pd.Timedelta(frequency)
        prediction_source = "趋势兜底"
        prediction_error = remote_result.error
    else:
        future_values = np.asarray(remote_result.values, dtype=float)
        future_delta = remote_result.interval
        prediction_source = remote_result.model
        prediction_error = ""
    if remote_result.values is None:
        prediction_origin = history["time"].iloc[-1]
    else:
        seconds = int(future_delta.total_seconds())
        prediction_origin = pd.to_datetime(traffic["time"], errors="coerce", utc=True).max()
        prediction_origin = prediction_origin.floor(f"{seconds}s")
    future_time = [prediction_origin + future_delta * (i + 1) for i in range(steps)]
    future = pd.DataFrame(
        {
            "time": future_time,
            "vehicle_count": future_values,
            "average_speed": np.nan,
            "kind": "预测",
            "prediction_source": prediction_source,
            "prediction_error": prediction_error,
        }
    )
    return pd.concat([history, future], ignore_index=True)


def calculate_metrics(traffic: pd.DataFrame, detections: pd.DataFrame) -> dict[str, Any]:
    if traffic.empty:
        return {
            "vehicle_count": 0,
            "average_speed": 0.0,
            "hot_checkpoint": "暂无",
            "alerts": int(len(detections)),
        }

    checkpoint_counts = traffic.groupby("checkpoint_id")["vehicle_count"].sum()
    hot_checkpoint = str(checkpoint_counts.idxmax()) if not checkpoint_counts.empty else "暂无"
    speed_alerts = int(traffic["speed_kmh"].ge(55).fillna(False).sum())
    valid_speed = traffic.dropna(subset=["speed_kmh"])
    if valid_speed.empty:
        average_speed = 0.0
    else:
        weights = valid_speed["vehicle_count"].clip(lower=1)
        average_speed = float(np.average(valid_speed["speed_kmh"], weights=weights))
    return {
        "vehicle_count": int(traffic["vehicle_count"].sum()),
        "average_speed": round(average_speed, 1),
        "hot_checkpoint": hot_checkpoint,
        "alerts": speed_alerts + int(len(detections)),
    }


def checkpoint_points(traffic: pd.DataFrame) -> pd.DataFrame:
    if traffic.empty:
        return pd.DataFrame(columns=["checkpoint_id", "gps_lng", "gps_lat", "vehicle_count", "average_speed"])

    points = (
        traffic.dropna(subset=["gps_lng", "gps_lat"])
        .groupby("checkpoint_id", as_index=False)
        .agg(
            gps_lng=("gps_lng", "mean"),
            gps_lat=("gps_lat", "mean"),
            vehicle_count=("vehicle_count", "sum"),
            average_speed=("speed_kmh", "mean"),
        )
    )
    if points.empty:
        points = pd.DataFrame(
            [
                {
                    "checkpoint_id": name,
                    "gps_lng": lng,
                    "gps_lat": lat,
                    "vehicle_count": 0,
                    "average_speed": 0.0,
                }
                for name, (lng, lat) in CHECKPOINT_DEFAULTS.items()
            ]
        )
    return points
