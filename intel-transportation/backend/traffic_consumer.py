"""Consume traffic, plate-recognition and violation events into TimescaleDB."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import psycopg2
from kafka import KafkaConsumer

try:
    from .events import PlateRecognitionEvent, TrafficObservation, ViolationEvent, parse_event_time
except ImportError:  # Supports `python backend/traffic_consumer.py`.
    from events import PlateRecognitionEvent, TrafficObservation, ViolationEvent, parse_event_time


DB_DSN = os.getenv(
    "TIMESCALEDB_DSN",
    "host=localhost port=5432 user=postgres password=postgres dbname=traffic",
).strip()
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092").strip()
TRAFFIC_TOPIC = os.getenv("KAFKA_TOPIC_TRAFFIC", "traffic_stream").strip()
PLATE_TOPIC = os.getenv("KAFKA_TOPIC_PLATES", "plate_recognitions").strip()
VIOLATION_TOPIC = os.getenv("KAFKA_TOPIC_VIOLATIONS", "traffic_violations").strip()
CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "traffic-storage-v1").strip()
AUTO_OFFSET_RESET = os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest").strip().lower()


TRAFFIC_INSERT_SQL = """
    INSERT INTO traffic_gps (
        time, vehicle_id, checkpoint_id, camera_id, gps_lng, gps_lat,
        speed_kmh, vehicle_type, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (time, vehicle_id) DO NOTHING
"""

VIOLATION_INSERT_SQL = """
    INSERT INTO traffic_violations (
        time, event_id, plate, violation_type, checkpoint_id, camera_id,
        image_path, track_id, vehicle_type, confidence,
        bbox_x1, bbox_y1, bbox_x2, bbox_y2
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (time, event_id) DO NOTHING
"""

PLATE_INSERT_SQL = """
    INSERT INTO plate_recognitions (
        time, event_id, plate, checkpoint_id, camera_id, image_path,
        track_id, vehicle_type, ocr_confidence, detector_confidence,
        bbox_x1, bbox_y1, bbox_x2, bbox_y2
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (time, event_id) DO NOTHING
"""


def _bbox_values(bbox: list[int] | None) -> tuple[int | None, int | None, int | None, int | None]:
    return tuple(bbox) if bbox is not None else (None, None, None, None)


def traffic_row(payload: dict[str, Any]) -> tuple[Any, ...]:
    event = TrafficObservation.from_payload(payload)
    return (
        parse_event_time(event.time),
        event.vehicle_id,
        event.checkpoint_id,
        event.camera_id,
        event.gps_lng,
        event.gps_lat,
        event.speed_kmh,
        event.vehicle_type,
        event.confidence,
        *_bbox_values(event.bbox),
    )


def violation_row(payload: dict[str, Any]) -> tuple[Any, ...]:
    event = ViolationEvent.from_payload(payload)
    return (
        parse_event_time(event.time),
        event.event_id,
        event.plate,
        event.violation_type,
        event.checkpoint_id,
        event.camera_id,
        event.image_path,
        event.track_id,
        event.vehicle_type,
        event.confidence,
        *_bbox_values(event.bbox),
    )


def plate_row(payload: dict[str, Any]) -> tuple[Any, ...]:
    event = PlateRecognitionEvent.from_payload(payload)
    return (
        parse_event_time(event.time),
        event.event_id,
        event.plate,
        event.checkpoint_id,
        event.camera_id,
        event.image_path,
        event.track_id,
        event.vehicle_type,
        event.ocr_confidence,
        event.detector_confidence,
        *_bbox_values(event.bbox),
    )


def insert_event(cursor, topic: str, payload: dict[str, Any]) -> str:
    event_type = payload.get("event_type")
    if topic == VIOLATION_TOPIC or event_type == ViolationEvent.event_type:
        cursor.execute(VIOLATION_INSERT_SQL, violation_row(payload))
        return ViolationEvent.event_type
    if topic == PLATE_TOPIC or event_type == PlateRecognitionEvent.event_type:
        cursor.execute(PLATE_INSERT_SQL, plate_row(payload))
        return PlateRecognitionEvent.event_type
    if topic == TRAFFIC_TOPIC or event_type == TrafficObservation.event_type:
        cursor.execute(TRAFFIC_INSERT_SQL, traffic_row(payload))
        return TrafficObservation.event_type
    raise ValueError(f"unsupported Kafka event: topic={topic}, event_type={event_type}")


def _connect_with_retry():
    while True:
        try:
            return psycopg2.connect(DB_DSN, connect_timeout=5)
        except psycopg2.Error as exc:
            print(f"TimescaleDB 连接失败，5 秒后重试: {exc}")
            time.sleep(5)


def consume_and_insert():
    if AUTO_OFFSET_RESET not in {"earliest", "latest"}:
        raise ValueError("KAFKA_AUTO_OFFSET_RESET 必须为 earliest 或 latest")
    print(f"启动 Kafka -> TimescaleDB 消费者: {TRAFFIC_TOPIC}, {PLATE_TOPIC}, {VIOLATION_TOPIC}")
    connection = _connect_with_retry()
    consumer = KafkaConsumer(
        TRAFFIC_TOPIC,
        PLATE_TOPIC,
        VIOLATION_TOPIC,
        bootstrap_servers=[KAFKA_BROKER],
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        auto_offset_reset=AUTO_OFFSET_RESET,
        enable_auto_commit=False,
        group_id=CONSUMER_GROUP,
    )

    try:
        for message in consumer:
            try:
                with connection:
                    with connection.cursor() as cursor:
                        event_type = insert_event(cursor, message.topic, message.value)
                consumer.commit()
                print(f"入库成功: {event_type} offset={message.offset}")
            except (KeyError, TypeError, ValueError, psycopg2.Error) as exc:
                connection.rollback()
                print(f"事件处理失败，保留 offset 以便重试: {exc}; payload={message.value}")
                if connection.closed:
                    connection = _connect_with_retry()
                time.sleep(1)
    except KeyboardInterrupt:
        print("停止消费。")
    finally:
        consumer.close()
        connection.close()


if __name__ == "__main__":
    consume_and_insert()
