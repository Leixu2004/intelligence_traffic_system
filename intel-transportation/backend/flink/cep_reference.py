"""CEP 预警语义的本地复算，用来对拍 Flink 作业落库结果（9/21 课件）。

为什么需要它：`MATCH_RECOGNIZE` 的贪婪量词、`WITHIN` 的上限语义和
`AFTER MATCH SKIP PAST LAST ROW` 的跳过策略，口头上说清楚很难，
出问题时也无法判断是 SQL 写错还是引擎语义与预期不同。这里用纯 Python
按同一套规则复算一遍，Flink 结果与它不一致才算缺陷。

复算规则与 sql/congestion_cep.sql、sql/alert_level.sql 一一对应：
  1. 丢弃 speed_kmh 为空的记录（DEFINE 遇 NULL 按不匹配处理，会切断正在累积的低速段）；
  2. 按 camera_id 分组、事件时间升序，贪婪吃掉连续低速记录构成一段，
     段必须由「下一条不再低速」的事件闭合——Flink 的贪心量词不回溯，段尾没有这条事件就匹配不出来，
     所以「到数据结束仍在拥堵」的相机不出预警（与 SQL 同缺，不是复算偏差）；
  3. 段长（含收尾事件）超过 WITHIN 上限（config.CEP_MAX_SEGMENT_MINUTES）判不匹配；
     匹配至少 2 条低速记录，命中后从收尾事件之后继续找下一段（AFTER MATCH SKIP PAST LAST ROW）；
  4. 黄/绿来自 1 分钟滚动窗口均速，且只保留 avg ≥ 20 的窗口（<20 归 CEP 管）；
  5. 级别判定顺序照课件：紫 → 红 → 黄 → 绿，「持续多久」看段内首末事件之差。

用法：
    python -m backend.flink.cep_reference --file backend/flink/test_data.json
    python -m backend.flink.cep_reference --file ... --json out.json   # 供冒烟脚本比对
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import (
    ALERT_LEVELS,
    AMBER_SPEED_KMH,
    CEP_MAX_SEGMENT_MINUTES,
    PURPLE_MINUTES,
    PURPLE_SPEED_KMH,
    RED_MINUTES,
    RED_SPEED_KMH,
)

# 阈值与课件口径同源（backend/flink/config.py），复算与 SQL 校验共用一份常量，避免两处漂移。
# WITHIN 不再是分级用的 10/20 分钟，而是段长上限（ congestion_cep.sql 用同一个值）。
CEP_WITHIN = timedelta(minutes=CEP_MAX_SEGMENT_MINUTES)
RED_MIN_DURATION_MINUTES = RED_MINUTES
PURPLE_MIN_DURATION_MINUTES = PURPLE_MINUTES
RED_SPEED = RED_SPEED_KMH
PURPLE_SPEED = PURPLE_SPEED_KMH
AMBER_SPEED = AMBER_SPEED_KMH
LEVELS = ALERT_LEVELS


@dataclass(frozen=True)
class Observation:
    camera_id: str
    event_time: datetime
    speed_kmh: float


@dataclass(frozen=True)
class Alert:
    camera_id: str
    start_time: datetime
    end_time: datetime
    alert_level: str
    avg_speed: float
    min_speed: float
    low_cnt: int
    duration_min: float
    source: str

    def key(self) -> tuple[str, str, str]:
        return (self.camera_id, self.start_time.isoformat(timespec="seconds"), self.alert_level)


def parse_event_time(raw: str) -> datetime:
    """与 source_ddl.sql 的取值口径一致：前 19 个字符按 naive UTC 解析。"""
    return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")


def load_events(path: Path) -> tuple[list[Observation], int]:
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document["events"] if isinstance(document, dict) else document
    events: list[Observation] = []
    skipped = 0
    for record in records:
        speed = record.get("speed_kmh")
        if speed is None:
            skipped += 1
            continue
        events.append(
            Observation(
                camera_id=str(record["camera_id"]),
                event_time=parse_event_time(str(record["time"])),
                speed_kmh=float(speed),
            )
        )
    events.sort(key=lambda item: (item.event_time, item.camera_id))
    return events, skipped


def _runs(
    series: list[Observation], *, threshold: float, within: timedelta
) -> list[tuple[list[Observation], float]]:
    """按 congestion_cep.sql 的 MATCH_RECOGNIZE 语义切段。

    返回 (段内低速记录, 段长分钟)。三条硬约束都来自引擎实测：
    段尾必须有一条不再低速的事件来闭合；段（含闭合事件）跨度不得超过 within；
    段内不足 2 条不算一段。
    """
    runs: list[tuple[list[Observation], float]] = []
    index = 0
    total = len(series)
    while index < total:
        if series[index].speed_kmh >= threshold:
            index += 1
            continue
        end = index
        while end < total and series[end].speed_kmh < threshold:
            end += 1
        closer = series[end] if end < total else None
        matched = series[index:end]
        closed = (
            closer is not None
            and len(matched) >= 2
            and closer.event_time - series[index].event_time <= within
        )
        if closed:
            duration = (matched[-1].event_time - matched[0].event_time).total_seconds() / 60.0
            runs.append((matched, duration))
            index = end + 1  # AFTER MATCH SKIP PAST LAST ROW：闭合事件之后接着找下一段
        else:
            index = max(end, index + 1)
    return runs


def classify_level(avg_speed: float, duration_min: float) -> str:
    if avg_speed < PURPLE_SPEED and duration_min >= PURPLE_MIN_DURATION_MINUTES:
        return "PURPLE"
    if avg_speed < RED_SPEED and duration_min >= RED_MIN_DURATION_MINUTES:
        return "RED"
    if avg_speed < AMBER_SPEED:
        return "AMBER"
    return "GREEN"


def _alert(
    matched: Iterable[Observation],
    duration_min: float,
    source: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> Alert:
    rows = list(matched)
    speeds = [row.speed_kmh for row in rows]
    avg_speed = round(sum(speeds) / len(speeds), 1)
    return Alert(
        camera_id=rows[0].camera_id,
        start_time=start_time if start_time is not None else rows[0].event_time,
        end_time=end_time if end_time is not None else rows[-1].event_time,
        alert_level=classify_level(avg_speed, duration_min),
        avg_speed=avg_speed,
        min_speed=min(speeds),
        low_cnt=len(rows),
        duration_min=duration_min,
        source=source,
    )


def cep_alerts(events: list[Observation]) -> list[Alert]:
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for event in events:
        grouped[event.camera_id].append(event)

    alerts: list[Alert] = []
    for camera_id in sorted(grouped):
        series = sorted(grouped[camera_id], key=lambda item: item.event_time)
        for matched, duration in _runs(series, threshold=RED_SPEED, within=CEP_WITHIN):
            alerts.append(_alert(matched, duration, "cep"))
        for matched, duration in _runs(series, threshold=PURPLE_SPEED, within=CEP_WITHIN):
            alerts.append(_alert(matched, duration, "cep"))
    return alerts


def window_alerts(events: list[Observation]) -> list[Alert]:
    """1 分钟滚动窗口的黄/绿分支：只保留 avg ≥ 20，低于 20 的交给 CEP。"""
    buckets: dict[tuple[str, datetime], list[Observation]] = defaultdict(list)
    for event in events:
        floor_minute = event.event_time.replace(second=0, microsecond=0)
        buckets[(event.camera_id, floor_minute)].append(event)

    alerts: list[Alert] = []
    for (camera_id, window_start), rows in sorted(buckets.items()):
        speeds = [row.speed_kmh for row in rows]
        avg_speed = round(sum(speeds) / len(speeds), 1)
        if avg_speed < RED_SPEED:
            continue
        duration_min = 1.0  # SQL 侧是 TIMESTAMPDIFF(SECOND, start, end) / 60，闭区间端点差 60 秒
        alerts.append(
            _alert(
                rows,
                duration_min,
                "window",
                start_time=window_start,
                end_time=window_start + timedelta(seconds=59),
            )
        )
    return alerts


def expected_alerts(events: list[Observation]) -> list[Alert]:
    combined = cep_alerts(events) + window_alerts(events)
    return sorted(combined, key=lambda item: (item.camera_id, item.start_time, item.alert_level))


def load_db_rows(rows: Iterable[dict[str, Any]]) -> list[Alert]:
    """把 psql 输出的行转成同一结构，便于和复算结果比对。"""
    alerts: list[Alert] = []
    for row in rows:
        start = _aware(row["start_time"])
        end = _aware(row["end_time"])
        alerts.append(
            Alert(
                camera_id=str(row["camera_id"]),
                start_time=start,
                end_time=end,
                alert_level=str(row["alert_level"]),
                avg_speed=float(row["avg_speed"]),
                min_speed=float(row["min_speed"]),
                low_cnt=int(row["low_cnt"]),
                duration_min=float(row["duration_min"]),
                source=str(row["source"]),
            )
        )
    return alerts


def _aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace(" ", "T").replace("+00:00", ""))
    return parsed.replace(tzinfo=None)


def diff(expected: list[Alert], actual: list[Alert]) -> dict[str, list[dict[str, Any]]]:
    left = {item.key(): item for item in expected}
    right = {item.key(): item for item in actual}
    return {
        "missing": [asdict(left[key]) for key in sorted(left.keys() - right.keys())],
        "extra": [asdict(right[key]) for key in sorted(right.keys() - left.keys())],
    }


DB_COLUMNS = ("camera_id", "start_time", "end_time", "alert_level", "avg_speed", "min_speed", "low_cnt", "duration_min", "source")


def load_db_file(path: Path) -> list[Alert]:
    """读取 psql -A -t -F'|' 导出的 traffic_alerts 转储（无表头，列序见 DB_COLUMNS）。"""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(dict(zip(DB_COLUMNS, line.split("|"))))
    return load_db_rows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="CEP 预警语义本地复算")
    parser.add_argument("--file", default=str(Path(__file__).resolve().parent / "test_data.json"))
    parser.add_argument("--json", dest="json_out", default="", help="把期望预警写成 JSON，供与库中结果比对")
    parser.add_argument("--db", dest="db_dump", default="", help="psql 导出的实际结果，逐行比对并报告差异")
    args = parser.parse_args()

    events, skipped = load_events(Path(args.file))
    alerts = expected_alerts(events)
    by_level: dict[str, int] = {level: 0 for level in LEVELS}
    for alert in alerts:
        by_level[alert.alert_level] += 1

    print(f"事件 {len(events)} 条（丢弃缺失测速 {skipped} 条）→ 期望预警 {len(alerts)} 条")
    for level in LEVELS:
        print(f"  {level:<7} {by_level[level]}")
    for alert in alerts:
        print(
            f"  {alert.camera_id} {alert.start_time:%H:%M}→{alert.end_time:%H:%M} "
            f"{alert.alert_level:<7} avg={alert.avg_speed:5.1f} min={alert.min_speed:4.1f} "
            f"cnt={alert.low_cnt:<3} dur={alert.duration_min:4.1f} {alert.source}"
        )

    if args.json_out:
        payload = [
            {key: (value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value)
             for key, value in asdict(alert).items()}
            for alert in alerts
        ]
        Path(args.json_out).write_text(
            json.dumps({"simulation": True, "alerts": payload}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[写出] {args.json_out}")

    if args.db_dump:
        actual = load_db_file(Path(args.db_dump))
        report = diff(alerts, actual)
        print(f"[比对] 期望 {len(alerts)} 条 / 库中 {len(actual)} 条")
        for name in ("missing", "extra"):
            for item in report[name]:
                print(
                    f"  {name}: {item['camera_id']} {item['start_time']} {item['alert_level']} "
                    f"avg={item['avg_speed']} cnt={item['low_cnt']} {item['source']}"
                )
            if not report[name]:
                print(f"  {name}: 无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
