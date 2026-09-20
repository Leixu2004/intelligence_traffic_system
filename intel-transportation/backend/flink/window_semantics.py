"""滑动窗口的参考实现：用纯 Python 复算 SQL 作业期望得到的结果。

用途只有两个：
1. 单测锁住 HOP(size, slide) 的窗口归属与聚合语义；
2. 演示脚本给出「作业应当输出什么」的对照表，供集群跑通后逐行比对。
它不替代 Flink，也不构成任何性能或准确率指标。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

from ..events import parse_event_time


@dataclass(frozen=True)
class SpeedWindowStat:
    window_start: datetime
    camera_id: str
    avg_speed: float
    max_speed: float
    vehicle_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_start": self.window_start.astimezone(timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "camera_id": self.camera_id,
            "avg_speed": self.avg_speed,
            "max_speed": self.max_speed,
            "vehicle_count": self.vehicle_count,
        }


def _epoch_millis(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.astimezone(timezone.utc).timestamp() * 1000)


def hop_window_starts(
    event_time: datetime,
    slide_seconds: int,
    size_seconds: int,
) -> list[datetime]:
    """返回事件所属的全部滑动窗口起点（按时间升序）。

    窗口左闭右开，起点与 Unix 纪元起对齐（Flink HOP 的默认 offset），
    因此相邻窗口重叠 size-slide 秒，一条数据归属 size/slide 个窗口。
    """
    if slide_seconds <= 0 or size_seconds < slide_seconds:
        raise ValueError("要求 size_seconds >= slide_seconds > 0")
    slide_ms = slide_seconds * 1000
    size_ms = size_seconds * 1000
    millis = _epoch_millis(event_time)
    latest = (millis // slide_ms) * slide_ms
    starts: list[int] = []
    cursor = latest
    while cursor > millis - size_ms:
        starts.append(cursor)
        cursor -= slide_ms
    return [
        datetime.fromtimestamp(value / 1000, tz=timezone.utc) for value in reversed(starts)
    ]


def _speed_of(payload: Mapping[str, Any]) -> float | None:
    raw = payload.get("speed_kmh")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _round_half_up(values: Sequence[float]) -> float:
    total = sum(Decimal(str(value)) for value in values)
    average = total / Decimal(str(len(values)))
    return float(average.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def window_group_sizes(
    events: Iterable[Mapping[str, Any]],
    *,
    slide_seconds: int = 300,
    size_seconds: int = 600,
) -> dict[tuple[datetime, str], int]:
    """HAVING 过滤前的分组规模：(窗口起点, 相机) → 命中条数。"""
    return {
        (datetime.fromtimestamp(millis / 1000, tz=timezone.utc), camera_id): len(speeds)
        for (millis, camera_id), speeds in _group_speeds(events, slide_seconds, size_seconds).items()
    }


def _group_speeds(
    events: Iterable[Mapping[str, Any]],
    slide_seconds: int,
    size_seconds: int,
) -> dict[tuple[int, str], list[float]]:
    grouped: dict[tuple[int, str], list[float]] = defaultdict(list)
    for payload in events:
        speed = _speed_of(payload)
        if speed is None:
            continue
        camera_id = str(payload.get("camera_id") or payload.get("checkpoint_id") or "").strip()
        if not camera_id:
            continue
        try:
            event_time = parse_event_time(payload.get("time") or payload.get("timestamp"))
        except (ValueError, TypeError):
            continue
        for window_start in hop_window_starts(event_time, slide_seconds, size_seconds):
            grouped[(_epoch_millis(window_start), camera_id)].append(speed)
    return grouped


def aggregate_speed_stats(
    events: Iterable[Mapping[str, Any]],
    *,
    slide_seconds: int = 300,
    size_seconds: int = 600,
    min_vehicle_count: int = 6,
) -> list[SpeedWindowStat]:
    """复算 speed_stats_job.sql 的语义：空速度剔除、按 (窗口, 相机) 分组、HAVING COUNT > n。

    入参是 traffic_stream 的 payload 字典（含 time / camera_id / speed_kmh）。
    """
    grouped = _group_speeds(events, slide_seconds, size_seconds)
    stats: list[SpeedWindowStat] = []
    for (start_millis, camera_id), speeds in grouped.items():
        if len(speeds) < min_vehicle_count:
            # HAVING COUNT(*) > 5 —— min_vehicle_count 即 5 + 1
            continue
        stats.append(
            SpeedWindowStat(
                window_start=datetime.fromtimestamp(start_millis / 1000, tz=timezone.utc),
                camera_id=camera_id,
                avg_speed=_round_half_up(speeds),
                max_speed=max(speeds),
                vehicle_count=len(speeds),
            )
        )
    stats.sort(key=lambda stat: (_epoch_millis(stat.window_start), stat.camera_id))
    return stats
