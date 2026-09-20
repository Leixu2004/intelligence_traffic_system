# -*- coding: utf-8 -*-
"""Flink SQL 作业演示脚本（9/21 课件交付）。

四段输出：
  [1/4] 交付物清单        —— 课件要求的 6 份文件是否齐备
  [2/4] SQL 静态校验      —— 连接器参数 / Watermark / 窗口 / 列顺序 / 口令泄漏
  [3/4] 窗口语义复算      —— 纯 Python 参考实现，给出「作业应输出什么」的对照表
  [4/4] 集群连通性        —— 探测 Flink Web UI，未启动只报待办不算失败

第 3 段用的是本仓库自己生成的确定性合成车流（simulation=true），
只用于演示窗口语义，不是实测车速；第 4 段才需要 Docker。

用法：
    python backend/scripts/flink_smoke.py
    python backend/scripts/flink_smoke.py --json data/flink/smoke.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    """把 git 忽略的 .env 读进当前进程（已存在的环境变量优先）。"""
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

from backend.flink.config import load_flink_settings  # noqa: E402
from backend.flink.sql_checks import run_sql_checks  # noqa: E402
from backend.flink.window_semantics import (  # noqa: E402
    aggregate_speed_stats,
    hop_window_starts,
    window_group_sizes,
)

CAMERAS = {
    "CAM-01": "CP-NORTH-01",
    "CAM-02": "CP-SOUTH-02",
    "CAM-03": "CP-EAST-03",
}
BASE_TIME = datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc)
DURATION_MINUTES = 20
RANDOM_SEED = 20260921


def synthetic_events() -> list[dict[str, object]]:
    """确定性车流：CAM-01 通畅、CAM-02 中段拥堵（车速掉到 20 以下）、CAM-03 车流稀疏。

    稀疏相机用于展示 HAVING COUNT(*) > 5 的过滤效果。
    """
    rng = random.Random(RANDOM_SEED)
    events: list[dict[str, object]] = []
    for minute in range(DURATION_MINUTES):
        for offset in range(8):
            moment = BASE_TIME + timedelta(minutes=minute, seconds=offset * 45 + rng.randint(0, 20))
            for camera_id, checkpoint_id in CAMERAS.items():
                if camera_id == "CAM-03" and rng.random() < 0.75:
                    continue  # CAM-03 流量稀疏
                if 6 <= minute < 12 and camera_id == "CAM-02":
                    speed = rng.uniform(8.0, 19.0)  # 拥堵时段
                else:
                    speed = rng.uniform(45.0, 88.0)
                events.append(
                    {
                        "event_type": "traffic_observation",
                        "time": moment.isoformat(timespec="milliseconds"),
                        "vehicle_id": f"TRACK-{len(events) + 1:05d}",
                        "checkpoint_id": checkpoint_id,
                        "camera_id": camera_id,
                        "speed_kmh": round(speed, 1),
                    }
                )
    events.sort(key=lambda event: str(event["time"]))
    return events


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def show_artifacts(settings) -> list[dict[str, object]]:
    _section("[1/4] 交付物清单（课件《Flink SQL实时作业》）")
    rows = []
    for path in (*settings.artifacts, settings.db_ddl):
        exists = path.is_file()
        size = path.stat().st_size if exists else 0
        rows.append({"path": str(path.relative_to(ROOT)), "exists": exists, "bytes": size})
        print(f"  {'[有]' if exists else '[缺]'} {rows[-1]['path']:<44} {size:>7} B")
    return rows


def show_sql_checks(settings) -> bool:
    _section("[2/4] SQL 静态校验")
    result = run_sql_checks(settings)
    for check in result["checks"]:
        print(f"  {'[OK ]' if check['passed'] else '[FAIL]'} {check['name']:<34} {check['message']}")
    print(f"  合计 {result['total']} 项，失败 {result['failed_count']} 项")
    return bool(result["passed"])


def show_window_semantics(settings) -> dict[str, object]:
    _section("[3/4] 窗口语义复算（本地参考实现，非集群实测）")
    events = synthetic_events()
    with_speed = [event for event in events if event.get("speed_kmh") is not None]
    groups = window_group_sizes(
        with_speed,
        slide_seconds=settings.hop_slide_seconds,
        size_seconds=settings.hop_size_seconds,
    )
    stats = aggregate_speed_stats(
        with_speed,
        slide_seconds=settings.hop_slide_seconds,
        size_seconds=settings.hop_size_seconds,
        min_vehicle_count=settings.min_vehicle_count,
    )
    overlap = {
        len(hop_window_starts(event, settings.hop_slide_seconds, settings.hop_size_seconds))
        for event in (BASE_TIME, BASE_TIME + timedelta(seconds=1), BASE_TIME + timedelta(seconds=299))
    }
    print(f"  合成车流: {len(events)} 条 (simulation=true)，有效测速 {len(with_speed)} 条")
    print(f"  窗口参数: size={settings.hop_size_seconds // 60}min slide={settings.hop_slide_seconds // 60}min")
    print(f"  每条数据归属窗口数: {sorted(overlap)} —— 相邻窗口重叠，所以恒为 2")
    print(f"  窗口分组数: {len(groups)} → HAVING COUNT(*) > {settings.min_vehicle_count - 1} 后剩 {len(stats)}")
    print("  前 8 行（与集群跑通后 speed_stats 表按 window_start,camera_id 排序的结果逐行比对）:")
    print("    window_start            camera_id  avg_speed  max_speed  cnt")
    for stat in stats[:8]:
        row = stat.as_dict()
        print(
            f"    {row['window_start'][11:23]:<19}  {row['camera_id']:<9}  "
            f"{row['avg_speed']:>8.1f}  {row['max_speed']:>9.1f}  {row['vehicle_count']:>3}"
        )
    slow = [stat for stat in stats if stat.camera_id == "CAM-02" and stat.avg_speed < 25]
    if slow:
        print(f"  拥堵窗口示例: CAM-02 平均 {slow[0].avg_speed} km/h @ {slow[0].window_start:%H:%M}（9/22 CEP 的判定输入）")
    cameras = sorted({stat.camera_id for stat in stats})
    thin = sorted(
        [(start, camera, size) for (start, camera), size in groups.items()
         if size < settings.min_vehicle_count],
        key=lambda item: (item[0], item[1]),
    )
    print(f"  出窗相机: {cameras}")
    if thin:
        start, camera, size = thin[0]
        print(f"  被 HAVING 滤掉的分组: {len(thin)} 个，例如 {camera} @ {start:%H:%M} 只有 {size} 条")
    else:
        print(f"  被 HAVING 滤掉的分组: 0 个（每窗都达到 {settings.min_vehicle_count} 条）")
    return {
        "events": len(events),
        "windows": len(stats),
        "windows_per_event": sorted(overlap),
        "cameras": cameras,
        "rows": [stat.as_dict() for stat in stats],
        "simulation": True,
    }


def probe_cluster(settings) -> dict[str, object]:
    _section("[4/4] Flink 集群连通性")
    url = f"{settings.web_ui_url}/overview"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"  [待办] Web UI {url} 不可达：{type(exc).__name__}")
        print("         启动方式：bash backend/flink/submit.sh start（需要 Docker Desktop 运行中）")
        return {"reachable": False, "reason": type(exc).__name__}
    print(f"  [OK] {url} 可达")
    for key in ("taskmanagers", "slots-total", "slots-available", "jobs-running", "version"):
        print(f"       {key:<16} {payload.get(key)}")
    return {"reachable": True, "overview": payload}


def main() -> int:
    parser = argparse.ArgumentParser(description="Flink SQL 作业演示与自检")
    parser.add_argument("--json", dest="json_path", default=None, help="把结果写入 JSON 文件")
    args = parser.parse_args()

    settings = load_flink_settings()
    artifacts = show_artifacts(settings)
    sql_ok = show_sql_checks(settings)
    semantics = show_window_semantics(settings)
    cluster = probe_cluster(settings)

    _section("结论")
    print(f"  交付物齐备: {all(row['exists'] for row in artifacts)}")
    print(f"  SQL 静态校验: {'通过' if sql_ok else '未通过'}")
    print(f"  集群实测: {'已完成' if cluster.get('reachable') else '待办（未检测到 Flink Web UI）'}")
    print("  说明: 第 3 段是本仓库的合成车流 + 本地参考实现，只证明窗口语义，不作为实测车速证据。")

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "settings": {
                        "hop_slide_seconds": settings.hop_slide_seconds,
                        "hop_size_seconds": settings.hop_size_seconds,
                        "min_vehicle_count": settings.min_vehicle_count,
                        "kafka_topic": settings.kafka_topic,
                        "sink_table": settings.sink_table,
                    },
                    "artifacts": artifacts,
                    "sql_checks": run_sql_checks(settings),
                    "window_semantics": semantics,
                    "cluster": cluster,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  结果已写入 {target}")

    return 0 if sql_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
