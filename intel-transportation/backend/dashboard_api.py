"""Unified traffic dashboard and ONNX prediction API.

The service keeps the existing dashboard endpoint stable while adding the
prediction API required by the September 9 serviceization task. TimescaleDB
is optional for local development: when it is unavailable, the API reads the
same local CSV already used by the Streamlit fallback.
"""

from __future__ import annotations

import logging
import math
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    import psycopg2
except ImportError:  # pragma: no cover - optional local database dependency
    psycopg2 = None

try:
    from .agent import (
        TrafficAgentService,
        TrafficReadRepository,
        TrafficToolGateway,
        load_agent_settings,
    )
    from .agent.router import router as agent_router
    from .crew import CrewService, load_crew_settings
    from .crew.router import router as crew_router
    from .prediction.contracts import CheckpointPredictRequest, PredictionData, PredictionResponse, PredictRequest
    from .prediction.service import PredictionService
    from .rag import RagService, load_rag_settings
    from .rag.router import router as rag_router
except ImportError:  # Supports `python backend/dashboard_api.py`.
    from agent import (
        TrafficAgentService,
        TrafficReadRepository,
        TrafficToolGateway,
        load_agent_settings,
    )
    from agent.router import router as agent_router
    from crew import CrewService, load_crew_settings
    from crew.router import router as crew_router
    from prediction.contracts import CheckpointPredictRequest, PredictionData, PredictionResponse, PredictRequest
    from prediction.service import PredictionService
    from rag import RagService, load_rag_settings
    from rag.router import router as rag_router


BACKEND_ROOT = Path(__file__).resolve().parent
INNER_ROOT = BACKEND_ROOT.parent
PROJECT_ROOT = INNER_ROOT.parent
_configured_traffic_path = os.getenv("TRAFFIC_RECORDS_PATH", "").strip()
LOCAL_DATA_PATHS = (
    [Path(_configured_traffic_path).expanduser().resolve()]
    if _configured_traffic_path
    else [INNER_ROOT / "data" / "china_lstm_demo_records.csv"]
) + [INNER_ROOT / "data" / "vehicle_records.csv", PROJECT_ROOT / "vehicle_records.csv"]
LOCAL_DETECTION_PATHS = [
    PROJECT_ROOT / "detections.csv",
    INNER_ROOT / "data" / "detections.csv",
]
MODEL_PATH = Path(
    os.getenv(
        "TRAFFIC_MODEL_PATH",
        str(
            BACKEND_ROOT
            / "prediction"
            / "models"
            / "traffic_lstm_china_kdd2017_candidate_v1.onnx"
        ),
    )
).expanduser().resolve()
TIMESCALEDB_DSN = os.getenv("TIMESCALEDB_DSN", "").strip()
PREDICTION_API_KEY = os.getenv("PREDICTION_API_KEY", "").strip()
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_cors_origins(value: str) -> tuple[str, ...]:
    origins = tuple(origin.strip() for origin in value.split(",") if origin.strip())
    if "*" in origins:
        raise ValueError("API_CORS_ORIGINS 不允许使用通配符 *")
    return origins


API_CORS_ORIGINS = _parse_cors_origins(
    os.getenv(
        "API_CORS_ORIGINS",
        "http://localhost:8501,http://127.0.0.1:8501",
    )
)
API_EXPOSE_INTERNAL_ERRORS = _env_flag("API_EXPOSE_INTERNAL_ERRORS")
LOGGER = logging.getLogger(__name__)


def _validate_runtime_security() -> None:
    if APP_ENV in {"production", "prod"} and not PREDICTION_API_KEY:
        raise RuntimeError("生产环境必须配置 PREDICTION_API_KEY")


def _now_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _load_local_records() -> list[dict[str, Any]]:
    for path in LOCAL_DATA_PATHS:
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        frame = frame.where(pd.notna(frame), None)
        records = frame.to_dict("records")
        for record in records:
            if record.get("time") is not None:
                record["time"] = str(record["time"])
            for key in ("gps_lng", "gps_lat", "speed_kmh"):
                if record.get(key) is not None:
                    record[key] = float(record[key])
        return records
    return []


def _load_local_detections() -> list[dict[str, Any]]:
    for path in LOCAL_DETECTION_PATHS:
        if not path.exists():
            continue
        frame = pd.read_csv(path).where(lambda value: pd.notna(value), None)
        records = frame.to_dict("records")
        for record in records:
            if record.get("time") is not None:
                record["time"] = str(record["time"])
            if record.get("confidence") is not None:
                record["confidence"] = float(record["confidence"])
        return records
    return []


def _load_timescale_records() -> list[dict[str, Any]]:
    if not TIMESCALEDB_DSN or psycopg2 is None:
        return []
    query = """
        SELECT bucket, checkpoint_id, total_vehicles, round(avg_speed::numeric, 2)
        FROM checkpoint_traffic_1m
        ORDER BY bucket DESC
        LIMIT 300;
    """
    with psycopg2.connect(TIMESCALEDB_DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    return [
        {
            "time": str(row[0]),
            "vehicle_id": f"{row[1]}-{row[0]}",
            "checkpoint_id": row[1],
            "vehicle_count": row[2],
            "average_speed": float(row[3]) if row[3] is not None else 0.0,
        }
        for row in rows
    ]


def _load_timescale_checkpoint_records(
    checkpoint_id: str,
    bin_seconds: int,
    history_points: int,
) -> list[dict[str, Any]]:
    if not TIMESCALEDB_DSN or psycopg2 is None:
        return []
    minutes_per_bin = max(1, math.ceil(bin_seconds / 60))
    row_limit = max(10, history_points * minutes_per_bin + minutes_per_bin)
    query = """
        SELECT bucket, checkpoint_id, total_vehicles, round(avg_speed::numeric, 2)
        FROM checkpoint_traffic_1m
        WHERE checkpoint_id = %s
        ORDER BY bucket DESC
        LIMIT %s;
    """
    with psycopg2.connect(TIMESCALEDB_DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (checkpoint_id, row_limit))
            rows = cursor.fetchall()
    return [
        {
            "time": str(row[0]),
            "vehicle_id": f"{row[1]}-{row[0]}",
            "checkpoint_id": row[1],
            "vehicle_count": row[2],
            "average_speed": float(row[3]) if row[3] is not None else 0.0,
        }
        for row in rows
    ]


def _load_timescale_detections() -> list[dict[str, Any]]:
    if not TIMESCALEDB_DSN or psycopg2 is None:
        return []
    query = """
        SELECT time, camera_id, checkpoint_id, plate, vehicle_type, confidence,
               violation_type, image_path
        FROM traffic_violations
        ORDER BY time DESC
        LIMIT 300;
    """
    with psycopg2.connect(TIMESCALEDB_DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    return [
        {
            "time": str(row[0]),
            "camera_id": row[1],
            "checkpoint_id": row[2],
            "plate": row[3],
            "vehicle_type": row[4],
            "confidence": float(row[5]) if row[5] is not None else None,
            "violation_type": row[6],
            "image_path": row[7],
        }
        for row in rows
    ]


def _traffic_records() -> tuple[list[dict[str, Any]], str, str]:
    if TIMESCALEDB_DSN:
        try:
            records = _load_timescale_records()
            if records:
                return records, "TimescaleDB", ""
        except Exception as exc:
            warning = f"TimescaleDB 不可用，已切换本地 CSV：{exc}"
        else:
            warning = "TimescaleDB 查询无数据，已切换本地 CSV"
    else:
        warning = "未配置 TIMESCALEDB_DSN，使用本地 CSV"

    records = _load_local_records()
    if records:
        return records, "local-csv", warning
    return [], "empty", f"{warning}；未找到本地 vehicle_records.csv"


def _detection_records() -> tuple[list[dict[str, Any]], str, str]:
    if TIMESCALEDB_DSN:
        try:
            records = _load_timescale_detections()
            if records:
                return records, "TimescaleDB", ""
        except Exception as exc:
            warning = f"TimescaleDB 违章查询不可用，已切换本地 CSV：{exc}"
        else:
            warning = "TimescaleDB 暂无违章数据，已切换本地 CSV"
    else:
        warning = "未配置 TIMESCALEDB_DSN，使用本地 detections.csv"

    records = _load_local_detections()
    if records:
        return records, "local-csv", warning
    return [], "empty", f"{warning}；未找到本地 detections.csv"


def _checkpoint_features(checkpoint_id: str, history_points: int) -> list[float]:
    series = _checkpoint_series(checkpoint_id, 60, history_points=history_points).tail(
        history_points
    )
    if len(series) < 2:
        raise ValueError(f"卡口 {checkpoint_id} 至少需要 2 个分钟级历史点")
    return [float(value) for value in series]


def _checkpoint_series(
    checkpoint_id: str,
    bin_seconds: int,
    *,
    history_points: int = 20,
    missing_bucket_policy: str = "zero_fill",
) -> pd.Series:
    records: list[dict[str, Any]] = []
    if TIMESCALEDB_DSN:
        try:
            records = _load_timescale_checkpoint_records(
                checkpoint_id,
                bin_seconds,
                history_points,
            )
        except Exception:
            records = []
    if not records:
        fallback_records, _, _ = _traffic_records()
        records = [
            record
            for record in fallback_records
            if str(record.get("checkpoint_id")) == checkpoint_id
        ]
    if not records:
        records = [
            record
            for record in _load_local_records()
            if str(record.get("checkpoint_id")) == checkpoint_id
        ]
    frame = pd.DataFrame(records)
    if frame.empty or "time" not in frame.columns or "checkpoint_id" not in frame.columns:
        raise ValueError("没有可用于预测的流量记录")
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["time"])
    if "vehicle_count" not in frame.columns:
        frame["vehicle_count"] = 1
    frame["vehicle_count"] = pd.to_numeric(frame["vehicle_count"], errors="coerce").fillna(0)
    series = frame.set_index("time")["vehicle_count"].resample(f"{bin_seconds}s").sum(
        min_count=1
    ).sort_index()
    if series.empty:
        raise ValueError(f"卡口 {checkpoint_id} 没有有效时间序列")
    if missing_bucket_policy == "split_on_gap" and series.isna().any():
        last_gap_position = int(np.flatnonzero(series.isna().to_numpy())[-1])
        series = series.iloc[last_gap_position + 1 :]
    elif missing_bucket_policy == "zero_fill":
        series = series.fillna(0.0)
    elif series.isna().any():
        raise ValueError(f"不支持的缺失时间桶策略: {missing_bucket_policy}")
    return series.dropna()


def _checkpoint_model_features(
    checkpoint_id: str,
    service: PredictionService,
    history_points: int,
) -> list[Any]:
    required_points = service.history_steps or history_points
    series = _checkpoint_series(
        checkpoint_id,
        service.bin_seconds,
        history_points=required_points,
        missing_bucket_policy=service.missing_bucket_policy,
    ).tail(required_points)
    if len(series) < required_points:
        raise ValueError(
            f"卡口 {checkpoint_id} 需要 {required_points} 个 "
            f"{service.bin_seconds} 秒历史点，当前只有 {len(series)} 个"
        )
    if len(service.feature_columns) == 1:
        return [float(value) for value in series]

    try:
        local_index = series.index.tz_convert(service.timezone)
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"模型时区无效: {service.timezone}") from exc
    calendar_features = [
        service.calendar_feature_values(timestamp.to_pydatetime())
        for timestamp in local_index
    ]
    feature_values = {
        "vehicle_count": series.to_numpy(dtype=float),
        "hour": local_index.hour.to_numpy(dtype=float),
        "minute": local_index.minute.to_numpy(dtype=float),
        "day_of_week": local_index.dayofweek.to_numpy(dtype=float),
        "is_holiday": np.asarray(
            [item["is_holiday"] for item in calendar_features], dtype=float
        ),
        "is_makeup_workday": np.asarray(
            [item["is_makeup_workday"] for item in calendar_features], dtype=float
        ),
    }
    unknown = [column for column in service.feature_columns if column not in feature_values]
    if unknown:
        raise ValueError(f"卡口预测暂不支持模型特征: {', '.join(unknown)}")
    return [
        [float(feature_values[column][index]) for column in service.feature_columns]
        for index in range(len(series))
    ]


def _check_api_key(value: str | None) -> None:
    if PREDICTION_API_KEY and value != PREDICTION_API_KEY:
        raise HTTPException(status_code=401, detail="invalid X-API-Key")


def _agent_checkpoint_prediction(
    service: PredictionService,
    checkpoint_id: str,
    future_steps: int,
) -> dict[str, Any]:
    history_points = service.history_steps or 20
    features = _checkpoint_model_features(checkpoint_id, service, history_points)
    result = service.predict(
        [features],
        future_steps=future_steps,
        time_step_seconds=service.bin_seconds,
        target_column=service.target_column,
    )
    return {
        "forecast": result.forecast,
        "current_flow": result.current_flow,
        "unit": f"vehicles/{service.bin_seconds}s",
        "model": result.model_name,
        "backend": result.backend,
        "deployment_stage": service.deployment_stage,
        "bin_seconds": service.bin_seconds,
        "history_steps": service.history_steps,
        "latency_ms": result.latency_ms,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_runtime_security()
    service = PredictionService(MODEL_PATH)
    service.load()
    app.state.prediction_service = service
    app.state.api_key = PREDICTION_API_KEY
    traffic_repository = TrafficReadRepository(TIMESCALEDB_DSN)
    rag_service = RagService(load_rag_settings())
    app.state.rag_service = rag_service
    tool_gateway = TrafficToolGateway(
        traffic_records=traffic_repository.traffic_records,
        detection_records=traffic_repository.detection_records,
        predict_checkpoint=lambda checkpoint_id, future_steps: _agent_checkpoint_prediction(
            service,
            checkpoint_id,
            future_steps,
        ),
        law_search=rag_service.search_only,
    )
    app.state.traffic_agent_service = TrafficAgentService(load_agent_settings(), tool_gateway)
    app.state.crew_service = CrewService.build(load_crew_settings(), gateway=tool_gateway)
    try:
        yield
    finally:
        service.close()
        rag_service.close()


app = FastAPI(
    title="智慧交通预测服务 API",
    version="1.0.0",
    description="Traffic flow data access and metadata-driven ONNX forecast service.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(API_CORS_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)
app.include_router(agent_router)
app.include_router(rag_router)
app.include_router(crew_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    LOGGER.error(
        "Unhandled API error on %s",
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    content: dict[str, Any] = {
        "code": 500,
        "message": "internal server error",
        "timestamp": _now_timestamp(),
    }
    if API_EXPOSE_INTERNAL_ERRORS:
        content["detail"] = str(exc)
    return JSONResponse(
        status_code=500,
        content=content,
    )


@app.get("/health", tags=["运维"])
def health(request: Request) -> dict[str, Any]:
    service: PredictionService = request.app.state.prediction_service
    agent_service: TrafficAgentService = request.app.state.traffic_agent_service
    rag_service = getattr(request.app.state, "rag_service", None)
    crew_service = getattr(request.app.state, "crew_service", None)
    records, source, warning = _traffic_records()
    detections, detection_source, detection_warning = _detection_records()
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "service": "up",
            "environment": APP_ENV,
            "prediction_auth_configured": bool(PREDICTION_API_KEY),
            "model_backend": service.backend,
            "model_loaded": service.session is not None,
            "model": service.model_name,
            "model_type": service.model_type,
            "model_profile": service.model_profile,
            "model_deployment_stage": service.deployment_stage,
            "model_error": service.load_error,
            "traffic_agent": agent_service.health(),
            "traffic_rag": rag_service.health() if rag_service is not None else {"enabled": False, "available": False, "error": "not initialised"},
            "crew": crew_service.health() if crew_service is not None else {"enabled": False, "available": False, "error": "not initialised"},
            "traffic_source": source,
            "traffic_records": len(records),
            "warning": warning,
            "detection_source": detection_source,
            "detection_records": len(detections),
            "detection_warning": detection_warning,
        },
        "timestamp": _now_timestamp(),
    }


@app.get("/model/info", tags=["模型"])
def model_info(request: Request) -> dict[str, Any]:
    service: PredictionService = request.app.state.prediction_service
    return {"code": 200, "message": "success", "data": service.info(), "timestamp": _now_timestamp()}


@app.get("/api/traffic_trend", tags=["大屏数据"])
def traffic_trend() -> dict[str, Any]:
    records, source, warning = _traffic_records()
    return {
        "status": "success",
        "code": 200,
        "message": warning or "success",
        "source": source,
        "data": records,
        "timestamp": _now_timestamp(),
    }


@app.get("/api/detections", tags=["大屏数据"])
def detections() -> dict[str, Any]:
    records, source, warning = _detection_records()
    return {
        "status": "success",
        "code": 200,
        "message": warning or "success",
        "source": source,
        "data": records,
        "timestamp": _now_timestamp(),
    }


def _predict(request: Request, payload: PredictRequest, api_key: str | None) -> PredictionResponse:
    _check_api_key(api_key)
    started = time.perf_counter()
    service: PredictionService = request.app.state.prediction_service
    future_steps = payload.future_steps or service.max_future_steps
    time_step_seconds = payload.time_step_seconds or service.bin_seconds
    try:
        result = service.predict(
            payload.features,
            future_steps=future_steps,
            moving_average_window=payload.moving_average_window,
            time_step_seconds=time_step_seconds,
            target_column=payload.target_column,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    return PredictionResponse(
        data=PredictionData(
            flow=result.current_flow,
            forecast=result.forecast,
            flows=result.current_flows,
            forecasts=result.forecasts,
            unit=f"vehicles/{time_step_seconds}s",
            model=result.model_name,
            backend=result.backend,
            latency_ms=max(result.latency_ms, elapsed),
            checkpoint_id=payload.checkpoint_id,
            target_column=result.target_column,
        ),
        timestamp=_now_timestamp(),
    )


@app.post("/predict", response_model=PredictionResponse, tags=["预测"])
def predict(payload: PredictRequest, request: Request, x_api_key: str | None = Header(default=None)) -> PredictionResponse:
    return _predict(request, payload, x_api_key)


@app.post("/api/v1/predict/traffic-flow", response_model=PredictionResponse, tags=["预测"])
def predict_traffic_flow(
    payload: PredictRequest, request: Request, x_api_key: str | None = Header(default=None)
) -> PredictionResponse:
    return _predict(request, payload, x_api_key)


@app.post("/api/v1/predict/checkpoint", response_model=PredictionResponse, tags=["预测"])
def predict_checkpoint(
    payload: CheckpointPredictRequest,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> PredictionResponse:
    service: PredictionService = request.app.state.prediction_service
    try:
        features = _checkpoint_model_features(
            payload.checkpoint_id,
            service,
            payload.history_points,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _predict(
        request,
        PredictRequest(
            features=[features],
            future_steps=payload.future_steps,
            moving_average_window=payload.moving_average_window,
            time_step_seconds=service.bin_seconds,
            target_column=service.target_column,
            checkpoint_id=payload.checkpoint_id,
        ),
        x_api_key,
    )


if __name__ == "__main__":
    import uvicorn

    print("启动智慧交通预测服务 API：http://127.0.0.1:8000")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
