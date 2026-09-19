"""Kafka event contracts shared by the edge pipeline and backend consumer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar
from uuid import uuid4


SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_event_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            result = datetime.strptime(text, "%Y%m%d_%H%M%S")
    else:
        raise ValueError("event time is required")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _bbox(payload: dict[str, Any]) -> list[int] | None:
    value = payload.get("bbox")
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must contain four coordinates")
    return [int(coordinate) for coordinate in value]


@dataclass(frozen=True)
class TrafficObservation:
    event_type: ClassVar[str] = "traffic_observation"

    time: str
    vehicle_id: str
    checkpoint_id: str
    camera_id: str
    gps_lng: float | None = None
    gps_lat: float | None = None
    speed_kmh: float | None = None
    vehicle_type: str | None = None
    confidence: float | None = None
    bbox: list[int] | None = None
    schema_version: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {"event_type": self.event_type, **asdict(self)}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TrafficObservation":
        parsed_time = parse_event_time(payload.get("time") or payload.get("timestamp"))
        return cls(
            time=parsed_time.isoformat(),
            vehicle_id=_required_text(payload, "vehicle_id"),
            checkpoint_id=_required_text(payload, "checkpoint_id"),
            camera_id=str(payload.get("camera_id") or payload.get("checkpoint_id") or "UNKNOWN"),
            gps_lng=_optional_float(payload.get("gps_lng")),
            gps_lat=_optional_float(payload.get("gps_lat")),
            speed_kmh=_optional_float(payload.get("speed_kmh")),
            vehicle_type=str(payload["vehicle_type"]) if payload.get("vehicle_type") else None,
            confidence=_optional_float(payload.get("confidence")),
            bbox=_bbox(payload),
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class PlateRecognitionEvent:
    """A validated plate read that is independent from violation semantics."""

    event_type: ClassVar[str] = "plate_recognition"

    time: str
    plate: str
    checkpoint_id: str
    camera_id: str
    event_id: str = ""
    image_path: str | None = None
    track_id: str | None = None
    vehicle_type: str | None = None
    ocr_confidence: float | None = None
    detector_confidence: float | None = None
    bbox: list[int] | None = None
    schema_version: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = {"event_type": self.event_type, **asdict(self)}
        if not payload["event_id"]:
            payload["event_id"] = str(uuid4())
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PlateRecognitionEvent":
        parsed_time = parse_event_time(payload.get("time") or payload.get("timestamp"))
        return cls(
            time=parsed_time.isoformat(),
            plate=_required_text(payload, "plate"),
            checkpoint_id=str(payload.get("checkpoint_id") or "UNKNOWN"),
            camera_id=str(payload.get("camera_id") or payload.get("checkpoint_id") or "UNKNOWN"),
            event_id=str(payload.get("event_id") or uuid4()),
            image_path=str(payload["image_path"]) if payload.get("image_path") else None,
            track_id=str(payload["track_id"]) if payload.get("track_id") is not None else None,
            vehicle_type=str(payload["vehicle_type"]) if payload.get("vehicle_type") else None,
            ocr_confidence=_optional_float(payload.get("ocr_confidence") or payload.get("confidence")),
            detector_confidence=_optional_float(payload.get("detector_confidence") or payload.get("det_conf")),
            bbox=_bbox(payload),
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class ViolationEvent:
    event_type: ClassVar[str] = "traffic_violation"

    time: str
    plate: str
    violation_type: str
    checkpoint_id: str
    camera_id: str
    image_path: str
    event_id: str = ""
    track_id: str | None = None
    vehicle_type: str | None = None
    confidence: float | None = None
    bbox: list[int] | None = None
    schema_version: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = {"event_type": self.event_type, **asdict(self)}
        if not payload["event_id"]:
            payload["event_id"] = str(uuid4())
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ViolationEvent":
        parsed_time = parse_event_time(payload.get("time") or payload.get("timestamp"))
        return cls(
            time=parsed_time.isoformat(),
            plate=_required_text(payload, "plate"),
            violation_type=str(payload.get("violation_type") or payload.get("type") or "UNKNOWN"),
            checkpoint_id=str(payload.get("checkpoint_id") or "UNKNOWN"),
            camera_id=str(payload.get("camera_id") or payload.get("checkpoint_id") or "UNKNOWN"),
            image_path=str(payload.get("image_path") or ""),
            event_id=str(payload.get("event_id") or uuid4()),
            track_id=str(payload["track_id"]) if payload.get("track_id") is not None else None,
            vehicle_type=str(payload["vehicle_type"]) if payload.get("vehicle_type") else None,
            confidence=_optional_float(payload.get("confidence") or payload.get("conf")),
            bbox=_bbox(payload),
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        )
