"""向 traffic_stream 持续灌入卡口过车事件，供 Flink 作业消费（9/21 课件演示）。

只做一件事：按 backend/events.py 的 TrafficObservation 契约生产 JSON，
字段与真实边缘端保持一致（含 camera_id / checkpoint_id / speed_kmh / ISO-8601 UTC 时间），
这样 Flink Source DDL 不需要为演示数据特判。

注意：这是**演示数据源**，车速是模拟生成的，不能作为真实测速或预测准确率的证据。

用法：
    python -m backend.flink.feed_traffic_stream --duration 900 --interval 0.4
    # Docker 内：docker compose --profile flink up -d flink-traffic-feed
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

DEFAULT_TOPIC = "traffic_stream"
CAMERAS = (
    ("CAM-01", "CP-NORTH-01"),
    ("CAM-02", "CP-SOUTH-02"),
    ("CAM-03", "CP-EAST-03"),
)
VEHICLE_TYPES = ("car", "bus", "truck", "van")
CONGESTION_CAMERA = "CAM-02"
CONGESTION_SPEED_RANGE = (8.0, 19.0)
FREE_FLOW_SPEED_RANGE = (45.0, 88.0)


def event_stream(
    *,
    seed: int,
    start_at: float | None = None,
    congestion_after: float = 120.0,
    congestion_seconds: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """无限产事件：``congestion_after`` 秒后 CAM-02 进入低速段，用于观察窗口均值下滑。"""
    rng = random.Random(seed)
    origin = start_at if start_at is not None else time.time()
    counter = 0
    while True:
        elapsed = time.time() - origin
        in_congestion = congestion_after <= elapsed < congestion_after + congestion_seconds
        for camera_id, checkpoint_id in CAMERAS:
            counter += 1
            low, high = (
                CONGESTION_SPEED_RANGE
                if (in_congestion and camera_id == CONGESTION_CAMERA)
                else FREE_FLOW_SPEED_RANGE
            )
            yield {
                "event_type": "traffic_observation",
                "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "vehicle_id": f"FEED-{counter % 100000:05d}",
                "checkpoint_id": checkpoint_id,
                "camera_id": camera_id,
                "gps_lng": 116.4074,
                "gps_lat": 39.9042,
                "speed_kmh": round(rng.uniform(low, high), 1),
                "vehicle_type": rng.choice(VEHICLE_TYPES),
                "confidence": round(rng.uniform(0.6, 0.99), 3),
                "bbox": [0, 0, 0, 0],
                "schema_version": 1,
            }
        time.sleep(max(0.0, rng.uniform(0.6, 1.4)))


def main() -> int:
    parser = argparse.ArgumentParser(description="traffic_stream 演示数据生产者")
    parser.add_argument("--broker", default=os.getenv("KAFKA_BROKER", "localhost:9092"))
    parser.add_argument("--topic", default=os.getenv("KAFKA_TOPIC_TRAFFIC", DEFAULT_TOPIC))
    parser.add_argument("--duration", type=float, default=900.0, help="运行秒数，0 表示不限")
    parser.add_argument("--interval", type=float, default=0.0, help="每条事件后的额外休眠秒数")
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument("--dry-run", action="store_true", help="只打印事件，不连 Kafka")
    args = parser.parse_args()

    producer = None
    if not args.dry_run:
        try:
            from kafka import KafkaProducer
        except ImportError:
            print("[失败] 未安装 kafka-python：pip install kafka-python", flush=True)
            return 2
        try:
            producer = KafkaProducer(
                bootstrap_servers=args.broker,
                value_serializer=lambda payload: json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] 连接 Kafka 失败 broker={args.broker}: {type(exc).__name__}: {exc}", flush=True)
            return 1

    print(f"[启动] topic={args.topic} broker={args.broker} duration={args.duration}s dry_run={args.dry_run}", flush=True)
    origin = time.time()
    sent = 0
    try:
        for payload in event_stream(seed=args.seed):
            if args.dry_run:
                print(json.dumps(payload, ensure_ascii=False), flush=True)
            elif producer is not None:
                producer.send(args.topic, payload)
            sent += 1
            if args.interval:
                time.sleep(args.interval)
            if args.duration and time.time() - origin >= args.duration:
                break
    except KeyboardInterrupt:
        print("[停止] 收到 Ctrl+C", flush=True)
    finally:
        if producer is not None:
            producer.flush(timeout=10)
            producer.close(timeout=10)
    print(f"[完成] 共产出 {sent} 条 traffic_observation 事件", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
