"""回放 backend/flink/test_data.json 到 traffic_stream，驱动 9/21 的 CEP 预警作业。

与 feed_traffic_stream.py 的区别只在数据来源：那份是持续随机产事件，这份是把固定数据集
按顺序发一遍，结果可复算（见 cep_reference.py），因此能作为验收证据而不是「跑起来看看」。

事件时间写的是数据集里的 2026-09-21T14:xx，而 Kafka 的日志时间是当前时间：
Flink 只按 event_time + WATERMARK 判窗口，所以没问题；但 `scan.startup.mode` 必须是
latest-offset（默认）且**先提交作业再灌数据**，否则历史消息已被跳过。
--sleep 默认 0，即一瞬间灌完，靠 watermark 推进把低速段闭合。

用法：
    python -m backend.flink.feed_cep_events --file backend/flink/test_data.json
    python -m backend.flink.feed_cep_events --dry-run        # 只打印，不连 Kafka
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_TOPIC = "traffic_stream"
DEFAULT_FILE = Path(__file__).resolve().parent / "test_data.json"


def to_observation(record: dict[str, Any]) -> dict[str, Any]:
    """补齐 backend/events.py::TrafficObservation 的必填字段，保持与真实边缘端同构。"""
    return {
        "event_type": "traffic_observation",
        "time": record["time"],
        "vehicle_id": record.get("vehicle_id", "UNKNOWN"),
        "checkpoint_id": record["checkpoint_id"],
        "camera_id": record["camera_id"],
        "gps_lng": 116.4074,
        "gps_lat": 39.9042,
        "speed_kmh": record.get("speed_kmh"),
        "vehicle_type": record.get("vehicle_type", "car"),
        "confidence": record.get("confidence", 0.93),
        "bbox": [0, 0, 0, 0],
        "schema_version": 1,
    }


def load(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document["events"] if isinstance(document, dict) else document
    return [to_observation(record) for record in records]


def main() -> int:
    parser = argparse.ArgumentParser(description="CEP 测试数据集回放")
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    parser.add_argument("--broker", default=os.getenv("KAFKA_BROKER", "localhost:9092"))
    parser.add_argument("--topic", default=os.getenv("KAFKA_TOPIC_TRAFFIC", DEFAULT_TOPIC))
    parser.add_argument("--sleep", type=float, default=0.0, help="每条事件后的休眠秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打印事件，不连 Kafka")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"[失败] 找不到数据集 {path}", flush=True)
        return 2
    payloads = load(path)

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
                value_serializer=lambda body: json.dumps(body, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] 连接 Kafka 失败 broker={args.broker}: {type(exc).__name__}: {exc}", flush=True)
            return 1

    print(
        f"[启动] topic={args.topic} broker={args.broker} events={len(payloads)} "
        f"dry_run={args.dry_run}（模拟数据，非真实测速）",
        flush=True,
    )
    for payload in payloads:
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        elif producer is not None:
            # 按 camera_id 取 key：同 key 恒定落同一分区、分区内有序，
            # MATCH_RECOGNIZE 收到的同一相机事件才按事件时间递增（并行度 > 1 时必须如此）。
            # 本机实测 3 个相机 key 全部哈希到 partition 3（kafka-get-offsets 可复核），
            # 单分区并行时带不带 key 到达顺序相同——CEP 不出预警的原因是 PATTERN 缺闭合条件，
            # 不是分区乱序（见 sql/congestion_cep.sql 头注）。
            producer.send(
                args.topic,
                key=payload["camera_id"].encode("utf-8"),
                value=payload,
            )
        if args.sleep:
            time.sleep(args.sleep)
    if producer is not None:
        producer.flush(timeout=15)
        producer.close(timeout=10)
    print(f"[完成] 已回放 {len(payloads)} 条事件", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
