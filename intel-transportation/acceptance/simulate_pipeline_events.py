"""Run a deterministic software-only acceptance path through Kafka and TimescaleDB.

This module deliberately emits synthetic events. It verifies service contracts and
container wiring; it does not measure detector, OCR, camera, GPU, or edge-device
accuracy and performance.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from kafka import KafkaProducer
import psycopg2

from backend.events import PlateRecognitionEvent, TrafficObservation, ViolationEvent


DB_DSN = os.getenv("TIMESCALEDB_DSN", "").strip()
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka:29092").strip()
TRAFFIC_TOPIC = os.getenv("KAFKA_TOPIC_TRAFFIC", "traffic_stream").strip()
PLATE_TOPIC = os.getenv("KAFKA_TOPIC_PLATES", "plate_recognitions").strip()
VIOLATION_TOPIC = os.getenv("KAFKA_TOPIC_VIOLATIONS", "traffic_violations").strip()
API_URL = os.getenv("PREDICTION_API_URL", "http://prediction-api:8000").rstrip("/")
API_KEY = os.getenv("PREDICTION_API_KEY", "").strip()
TIMEOUT_SECONDS = int(os.getenv("ACCEPTANCE_TIMEOUT_SECONDS", "45"))


def build_simulated_events(run_id: str, event_time: str) -> list[tuple[str, dict[str, Any]]]:
    plate_event_id = str(uuid5(NAMESPACE_URL, f"{run_id}:plate"))
    violation_event_id = str(uuid5(NAMESPACE_URL, f"{run_id}:violation"))
    common = {
        "time": event_time,
        "checkpoint_id": "SIM-CP-001",
        "camera_id": "SIM-CAM-001",
    }
    events = [
        (
            TRAFFIC_TOPIC,
            TrafficObservation(
                **common,
                vehicle_id=f"SIM-VEHICLE-{run_id}",
                gps_lng=116.4074,
                gps_lat=39.9042,
                speed_kmh=None,
                vehicle_type="car",
                confidence=0.9,
                bbox=[100, 120, 360, 300],
            ).to_payload(),
        ),
        (
            PLATE_TOPIC,
            PlateRecognitionEvent(
                **common,
                event_id=plate_event_id,
                plate="京A12345",
                track_id=run_id,
                vehicle_type="car",
                ocr_confidence=0.96,
                detector_confidence=0.91,
                bbox=[100, 120, 360, 300],
            ).to_payload(),
        ),
        (
            VIOLATION_TOPIC,
            ViolationEvent(
                **common,
                event_id=violation_event_id,
                plate="京A12345",
                violation_type="软件验收模拟事件",
                image_path="simulated://no-real-evidence-image",
                track_id=run_id,
                vehicle_type="car",
                confidence=0.96,
                bbox=[100, 120, 360, 300],
            ).to_payload(),
        ),
    ]
    for _, payload in events:
        payload["simulation"] = True
        payload["simulation_scope"] = "software_contract_and_wiring_only"
    return events


def _api_json(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    request = Request(f"{API_URL}{path}", data=data, headers=headers)
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_api() -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_error = ""
    while time.monotonic() < deadline:
        try:
            health = _api_json("/health")
            if health.get("data", {}).get("model_loaded"):
                return health
            last_error = "prediction model is not loaded"
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"prediction API did not become ready: {last_error}")


def _verify_prediction() -> dict[str, Any]:
    features = [
        [30.0 + index, float(8 + index), float((index * 20) % 60), 2.0, 0.0, 0.0]
        for index in range(6)
    ]
    result = _api_json(
        "/predict",
        {
            "features": features,
            "future_steps": 1,
            "time_step_seconds": 1200,
            "target_column": "vehicle_count",
        },
    )
    if result.get("code") != 200 or not result.get("data", {}).get("forecast"):
        raise RuntimeError(f"prediction API returned an invalid result: {result}")
    return result


def _publish(events: list[tuple[str, dict[str, Any]]]) -> None:
    producer = KafkaProducer(
        bootstrap_servers=[KAFKA_BROKER],
        value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
        acks="all",
        retries=5,
    )
    try:
        for topic, payload in events:
            producer.send(topic, payload).get(timeout=10)
        producer.flush(timeout=10)
    finally:
        producer.close(timeout=10)


def _wait_for_rows(run_id: str, plate_event_id: str, violation_event_id: str) -> dict[str, bool]:
    queries = {
        "traffic_observation": (
            "SELECT EXISTS(SELECT 1 FROM traffic_gps WHERE vehicle_id = %s)",
            (f"SIM-VEHICLE-{run_id}",),
        ),
        "plate_recognition": (
            "SELECT EXISTS(SELECT 1 FROM plate_recognitions WHERE event_id = %s)",
            (plate_event_id,),
        ),
        "traffic_violation": (
            "SELECT EXISTS(SELECT 1 FROM traffic_violations WHERE event_id = %s)",
            (violation_event_id,),
        ),
    }
    deadline = time.monotonic() + TIMEOUT_SECONDS
    state = {name: False for name in queries}
    while time.monotonic() < deadline:
        with psycopg2.connect(DB_DSN, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                for name, (query, params) in queries.items():
                    cursor.execute(query, params)
                    state[name] = bool(cursor.fetchone()[0])
        if all(state.values()):
            return state
        time.sleep(1)
    return state


def main() -> int:
    if not DB_DSN:
        raise RuntimeError("TIMESCALEDB_DSN is required")
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    event_time = now.isoformat(timespec="milliseconds")
    events = build_simulated_events(run_id, event_time)
    health = _wait_for_api()
    prediction = _verify_prediction()
    _publish(events)
    plate_event_id = events[1][1]["event_id"]
    violation_event_id = events[2][1]["event_id"]
    rows = _wait_for_rows(run_id, plate_event_id, violation_event_id)
    report = {
        "status": "PASS" if all(rows.values()) else "FAIL",
        "mode": "simulated",
        "scope": "software_contract_and_wiring_only",
        "run_id": run_id,
        "events_persisted": rows,
        "prediction": {
            "model": prediction["data"]["model"],
            "backend": prediction["data"]["backend"],
            "forecast_points": len(prediction["data"]["forecast"]),
        },
        "api": {
            "model_loaded": health["data"]["model_loaded"],
            "deployment_stage": health["data"].get("model_deployment_stage"),
        },
        "not_proven": [
            "YOLO detector mAP",
            "complete-plate OCR accuracy",
            "camera-to-dashboard latency",
            "GPU, TensorRT, Jetson, Atlas, or INT8 performance",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
